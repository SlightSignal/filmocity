# Project format (open JSON, the single source of truth)
```json
{ "version": 1, "id": "…", "name": "…",
  "media": { "<id>": { "id":"…","name":"clip.mp4","path":"/abs/path","duration":12.3,"width":1920,"height":1080,"fps":29.97,"has_video":true,"has_audio":true,"thumb":"/thumbs/id.jpg","strip":"/thumbs/id_strip.jpg","wave":"/thumbs/id_wave.png" } },
  "sequences": [ { "id":"seq1","name":"Sequence 01","width":1080,"height":1920,"fps":30,"duration":null,
      "markers":[{"id":"…","time":4.2,"name":"hook lands"}],
      "tracks":[ { "id":"V1","kind":"video","index":1,"muted":false,"locked":false,
          "clips":[ { "id":"c1","media_id":"<id>","start":0.0,"in_":1.0,"out":4.0,"speed":1.0,
                      "transform":{"x":0,"y":0,"scale":1,"rotation":0,"opacity":1},
                      "transition_in":{"type":"dissolve","duration":0.5},"transition_out":null,
                      "audio":{"gain_db":-3,"fade_in":0,"fade_out":0.3,"linked":true} },
                    { "id":"t1","media_id":null,"start":2.0,"in_":0,"out":3.0,"speed":1,
                      "title":{"text":"Forty a year.","size":120,"color":"white","x":0,"y":0,"borderw":0} } ] },
        { "id":"A1","kind":"audio","index":1,"muted":false,"locked":false,"clips":[] } ] } ],
  "annotations": [ { "id":"…","ts":0,"actor":"human","target":{"sequence":"seq1","track":"V1","clip_id":"c1"},"label":"too long","note":"…" } ] }
```
Times are seconds (floats). `start` is timeline position; `in_`/`out` are source seconds; timeline duration = (out − in_)/speed. Video-track clips carry their source audio unless `audio.linked` is false; audio-track clips are audio-only. Title clips have `media_id: null` and a `title` object. Higher `index` composites above lower on video tracks. Additional clip keys: `sequence_id` (nested sequence, with `media_id: null`), `adjustment: true`, `mask{…}`, `audio_fx{…}`, `keyframes{prop:[{t,v,e}]}`, `color{…}`; media keys: `is_image`, `proxy`, `bin`; project keys: `bins[{id,name}]`, `proposals[]`; sequence keys: `in_point, out_point, captions[], caption_style{}`, markers with `name, color`. Everything is added as new keys without breaking readers.
