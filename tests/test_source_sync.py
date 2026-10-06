"""Real decoded source/export cues; no browser/framework substitutes."""
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from render_context import RenderContext
from source_sync_fixture import make_source, pcm, pictures, runs


class SourceSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity sync É's ")
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)

    def project(self, **options):
        source, metadata, command = make_source(self.root / str(len(list(self.root.iterdir()))), **options)
        self.source = source; self.source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self.probe = metadata
        self.clip = {'id':'c', 'media_id':'m', 'start':0, 'in_':0, 'out':1.5}
        self.seq = {'id':'s', 'name':'Sync', 'fps':20, 'width':64, 'height':48, 'tracks':[
            {'id':'V1','kind':'video','index':1,'clips':[self.clip]}, {'id':'A1','kind':'audio','index':1,'clips':[]}]}
        self.p = {'media':{'m':{'id':'m','name':'Source','path':str(source),'fps':20,'duration':1.5,'width':64,'height':48,
                              'has_video':True,'has_audio':True,'channels':1,'sample_rate':options.get('sample_rate',48000)}}, 'sequences':[self.seq]}
        return self.p

    def render(self, *, audio=False, preset=None):
        before = copy.deepcopy(self.p)
        out = self.root / ('output.wav' if audio else 'output.mkv')
        settings = {'format':'audio','acodec':'wav'} if audio else {'vcodec':'ffv1','acodec':'pcm_s16le'}
        with RenderContext(scratch_parent=str(self.root), stall_timeout=20) as context:
            engine.render(self.p,'s',str(out),{**settings,**(preset or {})},context=context)
        self.assertEqual(self.p,before); self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.source_hash)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        return out

    def audio_cues(self, output, expected, tolerance=1):
        actual = runs(pcm(output))
        want = [(round(a*48000),round(b*48000)) for a,b in expected]
        self.assertEqual(len(actual),len(want),(actual,want))
        for got, ref in zip(actual,want):
            for a,b in zip(got,ref): self.assertLessEqual(abs(a-b),tolerance,(actual,want))

    def test_zero_and_nonzero_container_origins_have_identical_relative_cues(self):
        for origin in (0,5):
            with self.subTest(origin=origin):
                self.project(origin=origin); out=self.render()
                self.audio_cues(out,[(.1,.15),(.6,.65)])
                self.assertEqual([i for i,v in enumerate(pictures(out)) if v>200],[2,12])

    def test_delayed_audio_does_not_jump_to_picture_start(self):
        self.project(audio_offset=.375); self.audio_cues(self.render(),[(.475,.525),(.975,1.025)])

    def test_audio_leading_picture_preserves_picture_delay(self):
        self.project(video_offset=.25); out=self.render()
        self.audio_cues(out,[(.1,.15),(.6,.65)])
        self.assertEqual([i for i,v in enumerate(pictures(out)) if v>200],[7,17])

    def test_delayed_audio_with_nonzero_origin_is_normalized_once(self):
        self.project(audio_offset=.375,origin=5); self.audio_cues(self.render(),[(.475,.525),(.975,1.025)])

    def test_source_trim_and_sequence_placement_preserve_relative_offset(self):
        self.project(audio_offset=.375); self.clip.update(in_=.2,out=1.2,start=.25)
        self.audio_cues(self.render(audio=True),[(.525,.575),(1.025,1.075)])

    def test_subclip_offset_is_applied_once_before_source_trim(self):
        self.project(audio_offset=.375)
        self.p['media']['sub']={**self.p['media']['m'],'id':'sub','subclip_of':'m','sub_in':.15}
        self.clip.update(media_id='sub',in_=.05,out=1.05,start=.25)
        self.audio_cues(self.render(audio=True),[(.525,.575),(1.025,1.075)])

    def test_trim_wholly_before_delayed_audio_returns_the_requested_silence(self):
        self.project(audio_offset=.375); self.clip['out']=.2
        out=self.render(audio=True); samples=pcm(out)
        self.assertEqual(len(samples),9600); self.assertEqual(runs(samples),[])

    def test_trim_after_audio_ends_returns_silence_while_picture_continues(self):
        self.project(duration=2); self.clip.update(in_=1.1,out=1.4)
        out=self.render(audio=True); self.assertEqual(len(pcm(out)),14400); self.assertEqual(runs(pcm(out)),[])

    def test_fractional_frame_placement_uses_samples_not_milliseconds(self):
        self.project(audio_offset=.375); self.seq['fps']=30000/1001; self.clip['start']=1001/30000
        self.audio_cues(self.render(audio=True),[(.475+1001/30000,.525+1001/30000),(.975+1001/30000,1.025+1001/30000)])

    def test_44100_audio_retains_cues_when_resampled_to_48000(self):
        self.project(audio_offset=.375,sample_rate=44100)
        self.audio_cues(self.render(audio=True),[(.475,.525),(.975,1.025)],tolerance=2)

    def test_in_out_export_trims_the_normalized_mix(self):
        self.project(audio_offset=.375); self.seq.update(in_point=.45,out_point=1.05)
        self.audio_cues(self.render(audio=True,preset={'range':True}),[(.025,.075),(.525,.575)])

    def test_fade_uses_clip_time_including_initial_silence(self):
        self.project(audio_offset=.375); self.clip['audio']={'fade_in':1,'constant_power':False}
        samples=pcm(self.render(audio=True))
        self.assertAlmostEqual(samples[24000],12000/32768*.5,delta=2/32768)
        self.assertEqual(samples[4800],0)

    def test_reverse_retains_source_head_and_tail_silence(self):
        self.project(audio_offset=.375); self.clip.update(out=1.375,reverse=True)
        self.audio_cues(self.render(audio=True),[(.35,.4),(.85,.9)])

    def test_rate_change_without_pitch_preservation_scales_the_gap_too(self):
        self.project(audio_offset=.375); self.clip.update(speed=2,audio={'maintain_pitch':False})
        self.audio_cues(self.render(audio=True),[(.2375,.2625),(.4875,.5125)],tolerance=2)

    def test_nonzero_start_of_both_streams_uses_the_earliest_container_origin(self):
        self.project(video_offset=.25,audio_offset=.375)
        out=self.render(); self.audio_cues(out,[(.225,.275),(.725,.775)])
        self.assertEqual([i for i,v in enumerate(pictures(out)) if v>200],[2,12])

    def test_trimmed_delayed_picture_keeps_its_position_in_the_sequence(self):
        self.project(video_offset=.25); self.clip.update(in_=.2,out=1.2,start=.25)
        self.assertEqual([i for i,v in enumerate(pictures(self.render())) if v>200],[8,18])

    def test_audio_output_length_is_rounded_to_samples_not_milliseconds(self):
        self.project(audio_offset=.375); self.clip.update(start=1001/30000,out=.700123)
        samples=pcm(self.render(audio=True))
        self.assertEqual(len(samples),round((.700123+1001/30000)*48000))

    def test_timestamp_gap_inside_audio_is_not_collapsed(self):
        self.project(audio_gap=.05)
        self.audio_cues(self.render(audio=True),[(.1,.15),(.65,.7)])

    def test_decoded_aac_delay_and_priming_keep_audible_cues(self):
        self.project(audio_offset=.375,audio_codec='aac')
        # Lossy transform coding rounds edit lists and rings at sharp edges;
        # record an explicit 1 ms bound instead of claiming sample equality.
        self.audio_cues(self.render(audio=True),[(.475,.525),(.975,1.025)],tolerance=48)

    def test_piecewise_constant_rate_audio_includes_silence_in_each_segment(self):
        self.project(audio_offset=.375)
        self.clip.update(audio={'maintain_pitch':False},time_remap=[{'t':0,'v':2,'e':'hold'},{'t':.25,'v':1}])
        self.audio_cues(self.render(audio=True),[(.2375,.275),(.725,.775)],tolerance=2)

    def test_44100_pitch_changing_retime_is_based_on_the_mix_sample_rate(self):
        self.project(audio_offset=.375,sample_rate=44100)
        self.clip.update(speed=2,audio={'maintain_pitch':False})
        self.audio_cues(self.render(audio=True),[(.2375,.2625),(.4875,.5125)],tolerance=2)

    def test_footage_interpretation_scales_source_gaps_and_audio_rate_together(self):
        self.project(audio_offset=.375)
        self.p['media']['m']['interpret_fps']=10
        self.clip.update(out=3,audio={'maintain_pitch':False})
        self.audio_cues(self.render(audio=True),[(.95,1.05),(1.95,2.05)],tolerance=2)

    def test_subframe_picture_placement_is_not_truncated_in_a_coarse_filter_timebase(self):
        self.project(); self.clip['start']=1001/30000
        out=self.render(); self.audio_cues(out,[(.1+1001/30000,.15+1001/30000),(.6+1001/30000,.65+1001/30000)])
        self.assertEqual([i for i,v in enumerate(pictures(out)) if v>200],[3,13])

    def test_separate_inputs_normalize_their_own_container_origins(self):
        self.project(origin=5,audio_offset=.375)
        source,_,_=make_source(self.root/'second input',origin=11)
        checksum=hashlib.sha256(source.read_bytes()).hexdigest()
        self.p['media']['second']={**self.p['media']['m'],'id':'second','path':str(source)}
        self.seq['tracks'][1]['clips'].append({'id':'other','media_id':'second','start':.2,'in_':0,'out':1.3})
        self.audio_cues(self.render(audio=True),[(.3,.35),(.475,.525),(.8,.85),(.975,1.025)])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),checksum)


if __name__=='__main__': unittest.main()
