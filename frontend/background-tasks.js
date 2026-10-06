/* Persistent task status. Polling never mutates an edit or resubmits work. */
(function (global) {
  const active = new Set(['queued', 'running', 'cancelling', 'publishing', 'applying']);
  function create(CR, document, pane, timers = global) {
    let pending = false, timer = null, signature = '', rows = [], busy = new Set(), stopped = false, page = 0, owner = '', offline = false;
    const reviews = new Map();
    const needsReview = task => ['analysis','render_replace','sync','audio_analysis','recipe','cover'].includes(task.kind);
    const reviewAdapter = task => ({analysis:CR.reviewAnalysisTask,render_replace:CR.reviewRenderReplaceTask,sync:CR.reviewSyncTask,audio_analysis:CR.reviewAudioTask,recipe:CR.reviewRecipeTask,cover:CR.reviewCoverTask})[task.kind];
    const applyAdapter = task => ({analysis:CR.applyAnalysisTask,render_replace:CR.applyRenderReplaceTask,sync:CR.applySyncTask,audio_analysis:CR.applyAudioTask,recipe:CR.applyRecipeTask})[task.kind];
    let previewKey = '', previewMedia = null;
    const el = (tag, text, cls) => { const n=document.createElement(tag); if (text !== undefined) n.textContent=text; if(cls)n.className=cls; return n; };
    const heading=el('h2','Background tasks'), status=el('p','', 'task-status'), list=el('div');
    status.setAttribute('role','status');status.setAttribute('aria-live','polite');
    pane.appendChild(heading);pane.appendChild(el('p','Media preparation runs in the background. Review analysis results before applying edits; transcripts and collected originals also wait for you to apply them.','task-note'));
    pane.appendChild(status);
    const controls=el('div','', 'task-controls');pane.appendChild(controls);
    const button=(parent,text,fn)=>{const b=el('button',text);b.type='button';b.addEventListener('click',fn);parent.appendChild(b);return b;};
    button(controls,'Refresh tasks',()=>refresh(true));button(controls,'View exports',()=>CR.showTab('queue'));
    const count=el('span');
    const previous=button(controls,'Previous tasks',()=>{page--;render();});
    const nextPage=button(controls,'Next tasks',()=>{page++;render();});controls.appendChild(count);
    const previewPane=el('section','', 'task-result-preview');previewPane.hidden=true;pane.appendChild(previewPane);pane.appendChild(list);
    function closePreview(){previewMedia?.pause?.();previewMedia?.removeAttribute('src');previewMedia?.load?.();previewMedia=null;previewKey='';previewPane.replaceChildren();previewPane.hidden=true;}
    function renderPreview(){
      const task=rows.find(t=>t.kind==='render_replace'&&t.status==='ready'&&reviews.has(t.id)),reviewed=task&&reviews.get(task.id),preview=reviewed?.result?.preview;
      const current=reviewed&&same(reviewed.context,CR.S.context)&&reviewed.context.revision===CR.S.context.revision&&(!task.sequence||task.sequence===CR.S.seq?.id);
      const expected=task&&'/api/tasks/'+encodeURIComponent(task.id)+'/render-replace/preview';
      if(!current||!preview||!['image','audio'].includes(preview.kind)||preview.url!==expected){closePreview();return;}
      const key=JSON.stringify([task.id,reviewed.context.revision,preview]);if(key===previewKey)return;
      closePreview();previewKey=key;previewPane.hidden=false;previewPane.appendChild(el('h3','Render review: '+task.name));
      const media=el(preview.kind==='image'?'img':'audio');previewMedia=media;media.src=preview.url;
      if(preview.kind==='image'){media.alt='Representative rendered picture';media.className='task-render-picture';}
      else {media.controls=true;media.preload='none';media.setAttribute('aria-label','Rendered audio listening preview');}
      media.addEventListener('error',()=>{if(previewMedia===media)message('The render preview could not load. Refresh the review and inspect the task result before applying.',true);});
      previewPane.appendChild(media);previewPane.appendChild(el('p',preview.description||(preview.kind==='image'?'One representative picture; verify full playback after applying.':'Listening preview; the replacement retains the lossless source.'),'task-note'));
    }
    function message(text,error=false){status.textContent=text;status.className=error?'task-status task-error':'task-status';}
    function same(a,b){return a&&b&&a.workspace===b.workspace&&a.project===b.project;}
    async function action(task, kind) {
      if(busy.has(task.id))return;
      if(!same(task.context,CR.S.context)){message('Open the task’s original project first.',true);return;}
      busy.add(task.id);render();
      try {
        let result;
        if(kind==='review' && needsReview(task)) {
          result=await reviewAdapter(task)(task.id);
          if(!same(result.context,CR.S.context)||result.context.revision!==CR.S.context.revision)throw new Error('The project changed. Review this result again.');
          reviews.clear();reviews.set(task.id,result);
        } else if(kind==='apply' && task.kind==='cover') {
          throw new Error('Cover output is read-only. Review and download the owned images; there is no timeline Apply.');
        } else if(kind==='apply' && needsReview(task)) {
          const reviewed=reviews.get(task.id);
          if(!reviewed||!same(reviewed.context,CR.S.context)||reviewed.context.revision!==CR.S.context.revision)throw new Error('Review this result against the current edit before applying it.');
          result=await applyAdapter(task)(task.id,reviewed);reviews.delete(task.id);
        } else if(kind==='apply' && task.kind==='collect') {
          result=await CR.applyMediaCollection(task.id,{...CR.S.context,sequence:CR.S.seq?.id});
        } else if(kind==='apply') {
          if(global.FilmocityWorkflow?.hasDrafts())throw new Error('Save or discard pending word corrections before applying another transcript.');
          if(task.sequence!==CR.S.seq?.id)throw new Error('Open this task’s sequence before applying its transcript.');
          result=await CR.applyBackgroundTranscript(task.id,{...CR.S.context,sequence:task.sequence});
        } else if(kind==='retry') result=await CR.retryBackgroundTask(task.id);
        else { result=await CR.api.json('POST','/api/tasks/'+encodeURIComponent(task.id)+'/cancel',{});reviews.delete(task.id); }
        message(result.warning || result.message || (kind==='review'?'Review the proposed changes below.':kind==='retry'?'Retry queued.':kind==='apply'?'Result applied.':'Cancellation requested.'),Boolean(result.warning));
      } catch(error){message(error.message||String(error),true);}
      finally{busy.delete(task.id);signature='';await refresh(true);}
    }
    function render() {
      const focused=document.activeElement?.dataset?.taskAction;
      list.replaceChildren();
      if(!rows.length)list.appendChild(el('p','No background tasks for this project. Import footage or start transcription.','task-note'));
      page=Math.max(0,Math.min(Math.max(0,Math.ceil(rows.length/30)-1),page));
      previous.disabled=page===0;nextPage.disabled=(page+1)*30>=rows.length;
      count.textContent=rows.length ? `Page ${page+1} of ${Math.ceil(rows.length/30)} · ${rows.length} tasks` : '';
      for(const task of rows.slice(page*30,(page+1)*30)){
        const row=el('section','', 'task-row');row.dataset.task=task.id;list.appendChild(row);
        row.appendChild(el('h3',task.name));
        const names={queued:'Queued',running:'Running',cancelling:'Cancelling',publishing:'Saving result',applying:'Applying result',ready:'Ready to apply',done:'Complete',applied:'Applied',cancelled:'Cancelled',error:'Failed',interrupted:'Interrupted'};
        const kinds={transcribe:'Transcription',media:'Media preparation',package:'Portable project package',package_import:'Project package import',collect:'Collect original media',analysis:'Source analysis',render_replace:'Render and Replace',sync:'Audio synchronization',audio_analysis:'Clip audio analysis',recipe:'Recipe',cover:'Cover images'};
        row.appendChild(el('p',(kinds[task.kind]||task.kind)+' · '+(task.status==='ready'&&needsReview(task)?'Ready for review':names[task.status]||task.status)));
        if(active.has(task.status)){
          const waiting=task.resource?.state==='waiting';
          row.appendChild(el('p',waiting?'Waiting for processing slot':task.stage||''));const progress=el('progress');progress.max=1;
          progress.setAttribute('aria-label',task.name+' progress');if(!waiting&&Number.isFinite(task.progress))progress.value=task.progress;row.appendChild(progress);
        }
        if(task.message)row.appendChild(el('p',task.message,task.status==='error'?'task-error':'task-note'));
        if(task.warning)row.appendChild(el('p',task.warning,'task-error'));
        const reviewed=needsReview(task)&&reviews.get(task.id);
        const currentReview=reviewed&&same(reviewed.context,CR.S.context)&&reviewed.context.revision===CR.S.context.revision&&(!task.sequence||task.sequence===CR.S.seq?.id)&&(task.kind!=='cover'||CR.coverReviewCurrent?.(reviewed));
        if(reviewed&&task.status==='ready'){
          const detail=el('section','', 'task-analysis-review'),summary=reviewed.plan?.summary;
          detail.appendChild(el('h4',task.kind==='cover'?'Review rendered covers':task.kind==='recipe'?({reel:'Review New Reel',talking_head:'Review Talking Head',explainer:'Review Explainer cards',variants:'Review Hook Variants'}[reviewed.result?.mode]||'Review recipe'):task.kind==='audio_analysis'?(reviewed.result?.mode==='beats'?'Review estimated beat markers':'Review clip audio gain'):task.kind==='sync'?'Review audio synchronization':task.kind==='render_replace'?'Review rendered replacement':reviewed.result?.kind==='scenes'?'Review scene cuts':reviewed.result?.kind==='remix'?'Review music remix':'Review silence removal'));
          if(summary && task.kind==='cover'){
            detail.appendChild(el('p',summary.message||'Review the rendered covers. Downloading does not edit the project.'));
            if(Number.isFinite(reviewed.result?.time))detail.appendChild(el('p',`Captured sequence frame: ${reviewed.result.time.toFixed(6)} s`));
            for(const warning of (summary.warnings||[]).slice(0,50))detail.appendChild(el('p',String(warning),'task-note'));
            if(currentReview)for(const [index,cover] of (reviewed.result?.covers||[]).slice(0,4).entries()){
              const expected=CR.coverArtifactUrl?.(task.id,index,reviewed.context);
              if(cover.index!==index||cover.url!==expected)continue;
              const image=el('img');image.src=cover.url;image.alt=`Rendered cover ${cover.width} × ${cover.height}`;image.className='task-render-picture';
              image.addEventListener('error',()=>{if(reviews.get(task.id)===reviewed)message('The cover preview could not load. Refresh its review before downloading.',true);});detail.appendChild(image);
              detail.appendChild(el('p',`${cover.width} × ${cover.height} · ${cover.size} bytes · SHA-256 ${cover.sha256}`,'task-note'));
              const link=el('a',`Download ${cover.width} × ${cover.height} PNG`);link.href=expected+'&download=1&revision='+encodeURIComponent(reviewed.context.revision);link.download=cover.filename;
              link.addEventListener('click',event=>{if(!CR.coverReviewCurrent?.(reviewed)||!same(reviewed.context,CR.S.context)||reviewed.context.revision!==CR.S.context.revision||task.sequence&&task.sequence!==CR.S.seq?.id){event.preventDefault();message('The cover owner changed. Refresh its review before downloading.',true);}});detail.appendChild(link);
            }
          }else if(summary && task.kind==='recipe'){
            for(const line of CR.recipeReviewLines(reviewed))detail.appendChild(el('p',line,'task-note'));
          }else if(summary){
            detail.appendChild(el('p',summary.message||'Review these positions before applying the edit.'));
            if(task.kind==='render_replace'){
              if(typeof summary.format==='string')detail.appendChild(el('p','Replacement format: '+summary.format));
              if(Number.isFinite(summary.duration))detail.appendChild(el('p',`Replacement duration: ${summary.duration.toFixed(6)} s`));
              if(reviewed.result?.preview_warning)detail.appendChild(el('p',String(reviewed.result.preview_warning),'task-error'));
            }
            if(Array.isArray(summary.tracks))detail.appendChild(el('p','Affected tracks: '+summary.tracks.join(', ')));
            for(const [field,label] of [['baked','Baked processing'],['retained','Processing kept editable']])if(Array.isArray(summary[field]))detail.appendChild(el('p',label+': '+summary[field].slice(0,30).join(', ')));
            if(reviewed.result?.kind==='remix'){
              const result=reviewed.result;
              if(Number.isFinite(result.requested)&&Number.isFinite(result.achieved))detail.appendChild(el('p',`Requested ${result.requested.toFixed(6)} s · Planned ${result.achieved.toFixed(6)} s`));
              detail.appendChild(el('p',Number.isFinite(result.bpm)?`Estimated tempo: ${result.bpm} BPM`:'No reliable tempo estimate; review the proposed source pieces.','task-note'));
              if(Number.isFinite(result.tempo_confidence))detail.appendChild(el('p',`Rhythm similarity: ${Math.max(0,Math.min(1,result.tempo_confidence)).toFixed(3)} / 1. Verify the estimated beat by listening; this score does not establish musical structure.`,'task-note'));
            }
            if(task.kind==='sync'){
              const result=reviewed.result||{};
              detail.appendChild(el('p','Positive offsets place a source later than the reference. Confidence is a match-strength score, not a probability or guaranteed alignment accuracy. Verify alignment by listening before accepting the edit.','task-note'));
              if(result.mode==='media')detail.appendChild(el('p','Source matching only. No timeline edit can be applied from this result.','task-note'));
              const matches=Array.isArray(result.matches)?result.matches:[],matchList=el('ol');
              for(const match of matches.slice(0,50)){
                if(!match||!Number.isFinite(match.offset))continue;
                const name=CR.S.proj?.media?.[match.media_id]?.name||String(match.id);
                const parts=[`${name}: ${match.offset>=0?'+':''}${match.offset.toFixed(6)} s`];
                if(match.id===result.reference)parts.push('reference');
                else {
                  if(typeof match.method==='string')parts.push(match.method==='pcm'?'waveform refinement':match.method==='envelope'?'loudness envelope':match.method);
                  if(Number.isFinite(match.resolution)&&match.resolution>0)parts.push(`resolution ${(match.resolution*1000).toFixed(3)} ms`);
                  if(Number.isFinite(match.correlation))parts.push(`correlation ${Math.max(-1,Math.min(1,match.correlation)).toFixed(3)}`);
                  if(Number.isFinite(match.waveform_correlation))parts.push(`waveform correlation ${Math.max(-1,Math.min(1,match.waveform_correlation)).toFixed(3)}`);
                  if(Number.isFinite(match.overlap_seconds))parts.push(`overlap ${match.overlap_seconds.toFixed(3)} s`);
                  if(Number.isFinite(match.confidence))parts.push(`confidence score ${Math.max(0,Math.min(1,match.confidence)).toFixed(3)} / 1`);
                  else if(typeof match.confidence==='string')parts.push(match.confidence);
                  if(match.polarity===-1)parts.push('opposite waveform polarity');
                }
                matchList.appendChild(el('li',parts.join(' · ')));
              }
              if(matchList.children.length)detail.appendChild(matchList);
              if(matches.length>50)detail.appendChild(el('p',`${matches.length-50} more matches in the downloaded result.`));
              const moves=Array.isArray(summary.moves)?summary.moves:[],moveList=el('ol');
              for(const move of moves.slice(0,50))if(move&&Number.isFinite(move.from)&&Number.isFinite(move.to)){
                const residual=Number.isFinite(move.residual)?` · rounding residual ${(move.residual*1000).toFixed(3)} ms`:'';
                const clip=CR.S.seq?.tracks?.flatMap(track=>track.clips||[]).find(clip=>clip.id===move.clip_id);
                const name=clip?.name||CR.S.proj?.media?.[clip?.media_id]?.name||String(move.clip_id);
                moveList.appendChild(el('li',`${name}: ${move.from.toFixed(6)} → ${move.to.toFixed(6)} s${residual}`));
              }
              if(moveList.children.length)detail.appendChild(moveList);
              if(moves.length>50)detail.appendChild(el('p',`${moves.length-50} more moves in the downloaded result.`));
            }
            if(task.kind==='audio_analysis'){
              const result=reviewed.result||{},measurements=Array.isArray(result.measurements)?result.measurements:[];
              detail.appendChild(el('p',result.scope==='media'?'Measured source window only; timeline clip, track and master processing are excluded.':'Measured isolated clip audio includes its effects, fades and automation. Track and master processing are excluded. Gain changes shift the existing base and gain points equally; verify the final mix separately.','task-note'));
              if(result.scope==='media')detail.appendChild(el('p','Source measurements only. This result cannot change timeline gain or markers.','task-note'));
              if(result.mode==='beats')detail.appendChild(el('p','These are detected onset candidates, not guaranteed beats, bars or downbeats. Tempo confidence describes rhythmic regularity; verify marker positions by listening.','task-note'));
              const items=el('ol'),shownWarnings=new Set(summary.warnings||[]);
              for(const value of measurements.slice(0,50)){
                if(!value)continue;
                const name=CR.S.proj?.media?.[value.media_id]?.name||String(value.id),parts=[name];
                for(const [field,label,unit] of [['peak_db','Sample peak','dBFS'],['rms_db','RMS','dBFS'],['integrated_lufs','Integrated loudness','LUFS'],['true_peak_dbtp','True peak','dBTP'],['loudness_range_lu','Loudness range','LU']])if(Number.isFinite(value[field]))parts.push(`${label} ${value[field].toFixed(2)} ${unit}`);
                if(value.silent)parts.push('silent / below the measurement gate');
                else if(result.mode==='loudness'&&!Number.isFinite(value.integrated_lufs))parts.push('Integrated loudness unavailable / below the measurement gate');
                if(result.mode==='beats'){
                  parts.push(Number.isFinite(value.bpm)?`Estimated tempo ${value.bpm.toFixed(2)} BPM`:'No reliable tempo estimate');
                  if(Number.isFinite(value.tempo_confidence))parts.push(`tempo confidence ${value.tempo_confidence.toFixed(3)} / 1`);
                  if(Number.isFinite(value.resolution))parts.push(`analysis grid ${(value.resolution*1000).toFixed(3)} ms (not guaranteed timing accuracy)`);
                  if(Array.isArray(value.beats))parts.push(`${value.beats.length} detected onset candidates`);
                }
                items.appendChild(el('li',parts.join(' · ')));
                for(const warning of (value.warnings||[]).slice(0,20))if(!shownWarnings.has(warning)){shownWarnings.add(warning);items.appendChild(el('li',String(warning),'task-note'));}
              }
              if(items.children.length)detail.appendChild(items);
              const gains=Array.isArray(summary.gains)?summary.gains:[],changes=el('ol');
              for(const gain of gains.slice(0,50))if(gain&&[gain.target,gain.delta_db,gain.from_db,gain.to_db].every(Number.isFinite)){
                const predicted=[Number.isFinite(gain.predicted_peak_db)?`predicted sample peak ${gain.predicted_peak_db.toFixed(2)} dBFS`:'',Number.isFinite(gain.predicted_true_peak_dbtp)?`predicted true peak ${gain.predicted_true_peak_dbtp.toFixed(2)} dBTP`:''].filter(Boolean);
                changes.appendChild(el('li',`${gain.clip_id}: target ${gain.target} · gain shift ${gain.delta_db>=0?'+':''}${gain.delta_db.toFixed(4)} dB · base ${gain.from_db} → ${gain.to_db} dB · ${Number.isInteger(gain.automation_points)?gain.automation_points:0} gain points shifted${predicted.length?' · '+predicted.join(' · '):''}`));
              }
              if(changes.children.length)detail.appendChild(changes);
              const markers=Array.isArray(summary.markers)?summary.markers:[],positions=el('ol');
              for(const marker of markers.slice(0,50))if(marker&&Number.isFinite(marker.time))positions.appendChild(el('li',`${marker.name||'Estimated beat'}: ${marker.time.toFixed(6)} s${CR.fmtTC?' · '+CR.fmtTC(marker.time):''}`));
              if(positions.children.length)detail.appendChild(positions);
              if(markers.length>50)detail.appendChild(el('p',`${markers.length-50} additional marker positions in the downloaded result.`));
            }
            for(const warning of (summary.warnings||reviewed.result?.warnings||[]).slice(0,50))detail.appendChild(el('p',String(warning),'task-note'));
            const positions=Array.isArray(summary.ranges)&&summary.ranges.length?summary.ranges:Array.isArray(summary.cuts)?summary.cuts:[],items=el('ol');
            for(const value of positions.slice(0,50)){
              const values=Array.isArray(value)?value:typeof value==='object'?[value.start,value.end]:[value];
              if(values.every(Number.isFinite))items.appendChild(el('li',values.map(t=>CR.fmtTC?CR.fmtTC(t):t.toFixed(6)+' s').join(' – ')));
            }
            if(items.children.length)detail.appendChild(items);
            if(positions.length>50)detail.appendChild(el('p',`${positions.length-50} additional positions. Download the analysis result for the complete source list.`));
            if(reviewed.result?.kind==='remix'&&Array.isArray(reviewed.result.segments)){
              const pieces=el('ol');for(const segment of reviewed.result.segments.slice(0,50))if(Number.isFinite(segment.in)&&Number.isFinite(segment.out))pieces.appendChild(el('li',`Source ${segment.in.toFixed(6)} – ${segment.out.toFixed(6)} s`));
              if(pieces.children.length)detail.appendChild(pieces);if(reviewed.result.segments.length>50)detail.appendChild(el('p',`${reviewed.result.segments.length-50} more source pieces in the downloaded result.`));
            }
          }else detail.appendChild(el('p','This analysis has no timeline target. Download its source results for reference.'));
          if(!currentReview)detail.appendChild(el('p','The project changed. Review the result again before applying it.','task-error'));
          row.appendChild(detail);
        }
        if(task.kind==='collect'&&task.result){
          const result=task.result;
          row.appendChild(el('p',`${result.verified} verified files · ${result.copied} copied · ${result.reused} reused`));
          const path=el('input');path.type='text';path.readOnly=true;path.value=result.folder;path.setAttribute('aria-label','Collected originals folder');path.addEventListener('click',()=>path.select());row.appendChild(path);
          row.appendChild(el('p',task.status==='applied'?'Relink applied. Undo restores the previous paths; copies are retained.':'Copies are retained. Only Apply collected paths changes the project; it can be undone.','task-note'));
        }
        if(task.result&&task.status==='done'&&['package','package_import'].includes(task.kind)){
          const result=task.result;
          row.appendChild(el('p',task.kind==='package'?'Share this complete folder:':'Imported project: '+result.name+'. Open it from File → Open Project.'));
          const path=el('input');path.type='text';path.readOnly=true;path.value=result.folder;path.setAttribute('aria-label','Completed package folder');path.addEventListener('click',()=>path.select());row.appendChild(path);
          row.appendChild(el('p',`${result.files} verified files · Manifest SHA-256: ${result.manifest_sha256}`));
          for(const warning of result.warnings||[])row.appendChild(el('p',warning,'task-error'));
          for(const omission of result.omissions||[])row.appendChild(el('p',omission,'task-note'));
        }
        const actions=el('div','', 'task-controls');row.appendChild(actions);
        function actionButton(label,kind,fn){const b=button(actions,label,fn||(()=>action(task,kind)));b.dataset.taskAction=task.id+':'+kind;b.disabled=busy.has(task.id);if(focused===b.dataset.taskAction)b.focus();return b;}
        if(['queued','running','ready'].includes(task.status))actionButton(task.status==='ready'?(task.kind==='collect'?'Keep current paths':'Discard result'):'Cancel','cancel');
        if(['error','cancelled','interrupted'].includes(task.status))actionButton('Retry','retry');
        if(task.status==='ready'){
          if(needsReview(task)){
            if(task.sequence&&task.sequence!==CR.S.seq?.id)actionButton('Open sequence','open',()=>{CR.switchSeq(task.sequence);signature='';render();});
            else {
              actionButton(reviewed?'Refresh review':'Review result','review');
              if(reviewed?.plan&&task.kind!=='cover'){const apply=actionButton('Apply reviewed changes','apply');apply.disabled=busy.has(task.id)||!currentReview||!reviewed.plan.ops?.length;}
            }
          }else if(task.kind==='collect')actionButton('Apply collected paths','apply');
          else if(task.sequence!==CR.S.seq?.id)actionButton('Open sequence','open',()=>{CR.switchSeq(task.sequence);signature='';render();});
          else actionButton('Apply transcript','apply');
        }
        if(task.kind==='analysis'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download analysis result');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-analysis-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='render_replace'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download render receipt');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-render-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='sync'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download synchronization result');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-sync-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='audio_analysis'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download clip audio result');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-audio-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='cover'&&task.status==='ready'){
          const a=el('a','Download cover receipt');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-cover-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='recipe'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download recipe result');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-recipe-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='transcribe'&&['ready','applied'].includes(task.status)){
          const a=el('a','Download transcript result');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-transcript-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.kind==='collect'&&task.result){
          const a=el('a','Download collection receipt');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-collection-receipt-'+task.id+'.json';actions.appendChild(a);
        }
        if(task.status==='done'&&['package','package_import'].includes(task.kind)){
          const a=el('a','Download package receipt');a.href='/api/tasks/'+encodeURIComponent(task.id)+'/result';a.download='Filmocity-package-receipt-'+task.id+'.json';actions.appendChild(a);
        }
      }
      renderPreview();
    }
    async function refresh(force=false){
      if(stopped||pending)return;
      if(timer!==null){timers.clearTimeout(timer);timer=null;}
      const expected={...CR.S.context};pending=true;
      const currentOwner=JSON.stringify([expected.workspace,expected.project]);
      if(owner!==currentOwner){owner=currentOwner;rows=[];reviews.clear();page=0;signature='';message('Loading tasks…');render();}
      try{
        const result=await CR.api.get('/api/tasks');
        if(!same(expected,CR.S.context))return;
        if(!same(expected,result.context))throw new Error('The active project changed. Refresh the editor before managing tasks.');
        if(!Array.isArray(result.tasks))throw new Error('Invalid task status response');
        const next=JSON.stringify([expected.workspace,expected.project,expected.revision,CR.S.seq?.id,result.tasks]);
        rows=result.tasks;
        const activeCount=rows.filter(t=>active.has(t.status)).length, readyCount=rows.filter(t=>t.status==='ready').length;
        const badge=document.getElementById('btnTasks');if(badge){badge.textContent='Tasks'+(activeCount+readyCount?' ('+(activeCount+readyCount)+')':'');badge.title=`${activeCount} active, ${readyCount} ready to apply`;}
        if(offline||status.textContent==='Loading tasks…'){message(`${activeCount} active · ${readyCount} ready to apply`);offline=false;}
        if(force||next!==signature){signature=next;render();}
        if(result.unavailable?.length)message(`${result.unavailable.length} task receipt(s) could not be read. Existing files were preserved.`,true);
      }catch(error){if(same(expected,CR.S.context)){offline=true;message('Task status unavailable; retrying. '+error.message,true);}}
      finally{pending=false;if(!stopped)timer=timers.setTimeout(()=>refresh(),rows.some(t=>active.has(t.status))?1200:5000);}
    }
    return {refresh,action,invalidateReviews(){reviews.clear();signature='';render();},stop(){stopped=true;closePreview();if(timer!==null)timers.clearTimeout(timer);},get rows(){return rows;}};
  }
  let controller;
  global.FilmocityTasks={create,refresh:()=>controller?.refresh(true),invalidateReviews:()=>controller?.invalidateReviews(),open:()=>{global.CR.showTab('tasks');controller?.refresh(true);}};
  if(typeof module!=='undefined')module.exports={create};
  if(global.document&&global.CR){
    const pane=global.document.getElementById('pane-tasks');
    if(pane){controller=create(global.CR,global.document,pane);controller.refresh();}
    global.document.getElementById('btnTasks')?.addEventListener('click',global.FilmocityTasks.open);
  }
})(typeof window==='undefined'?globalThis:window);
