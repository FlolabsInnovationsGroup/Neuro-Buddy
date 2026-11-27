/* Coach Atlas v0.3. Original three-tab UI, central persistent Repository.
   Optimized Neurobuddy 0.7.1 scientific engine runs server-side. Optional external AI
   analysis API (placeholder until configured) for generated HTML reports. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const BASE = '/api';
  const html = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const fmt = v => v===null||v===undefined?'Not available':typeof v==='number'?Number(v).toPrecision(5).replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,''):String(v);
  const state = {tab:'library',library:{teams:[]},preview:null,jobs:[],advanced:{teams:[]},target:null,selectedJob:null,advancedSelection:null,busy:false,aiReport:null,aiStatus:null,aiBusy:false,aiResult:null};
  const LIBRARY_TRANSFER_MIME = 'application/x-neurobuddy-library+json';
  const LIBRARY_TRANSFER_SCHEMA = 'neurobuddy.library-transfer/1';
  const signalExt = ['.set','.edf','.bdf','.vhdr','.fif','.fif.gz'];
  const isSignal = n => signalExt.some(ext => (n||'').toLowerCase().endsWith(ext));
  const fmtSize = bytes => bytes>=1048576?(bytes/1048576).toFixed(1)+' MB':Math.max(1,Math.round(bytes/1024))+' KB';
  const badge = (value) => `<span class="status ${['completed','ready','COMPLETE','Within range'].includes(value)?'ready':['failed','Abnormal'].includes(value)?'fail':''}">${html(value||'unknown').replaceAll('_',' ')}</span>`;
  const artifactUrl = (id,path) => `/api/v2/artifacts/${encodeURIComponent(id)}/${path.split('/').map(encodeURIComponent).join('/')}`;
  async function api(path,options={}){
    let response;
    try{response=await fetch(BASE+path,{...options,cache:'no-store'});}
    catch(e){throw new Error('Cannot reach the local Neurobuddy server. Start it with the README command. '+e.message);}
    const payload=await response.json().catch(()=>null);
    if(!response.ok){
      const detail=payload?.detail;
      if(response.status===404 && (!detail || detail==='Not Found'))
        throw new Error(`The EEG API endpoint ${BASE+path} is missing on ${location.origin}. Open http://127.0.0.1:8765/ using the Neurobuddy Uvicorn server, not VS Code Live Server.`);
      throw new Error(typeof detail==='string'?detail:JSON.stringify(detail||`Request failed (${response.status})`));
    }
    return payload;
  }
  const jsonOpt = (method,obj) => ({method,headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});
  function notify(message,type='normal') {const el=$('global-banner');el.textContent=message;el.className='banner'+(type==='error'?' error':type==='success'?' success':'');el.hidden=false;}
  async function safe(fn){try{await fn();}catch(e){notify(e.message,'error');console.error(e);}}
  function navigate(tab){
    if(!['library','analysis','advanced'].includes(tab))return;
    state.tab=tab;for(const b of document.querySelectorAll('[data-tab]'))b.classList.toggle('active',b.dataset.tab===tab);
    for(const id of ['library','analysis','advanced'])$('view-'+id).hidden=tab!==id;
    history.replaceState(null,'','#'+tab);
    if(tab==='library')safe(loadLibrary);
    if(tab==='analysis')safe(loadJobs);
    if(tab==='advanced')safe(loadAdvanced);
  }
  const receiverMarkup = () => `<div class="library-receiver" role="region" aria-label="Repository hierarchy drop reception area">
      <span class="receiver-icon" aria-hidden="true">⇩</span>
      <strong>Drop a Repository hierarchy here</strong>
      <p>Team <span aria-hidden="true">→</span> Athlete <span aria-hidden="true">→</span> Baseline / Testing EEG</p>
      <div class="receiver-folder-path" aria-hidden="true"><span>▱ Team</span><span>♙ Athlete</span><span>◫ Baseline</span><span>◫ Testing</span></div>
      <small>Receiver ready · Repository not connected yet</small>
    </div>`;
  function renderLibrary(){ window.NeurobuddyShelf.render(); renderTarget(); }
  // Future Repository contract. Metadata is intentionally preview-only here; never
  // mark a recording ready or execute an EEG analysis without server-side source data.
  function parseLibraryTransfer(input){
    const payload=typeof input==='string'?JSON.parse(input):input;
    if(!payload||typeof payload!=='object'||Array.isArray(payload)||payload.schema_version!==LIBRARY_TRANSFER_SCHEMA)
      throw new Error('Unsupported Repository object. Expected '+LIBRARY_TRANSFER_SCHEMA+'.');
    if(!Array.isArray(payload.teams)||!payload.teams.length||payload.teams.length>100)
      throw new Error('Transfer must contain 1–100 teams.');
    let athleteCount=0,recordingCount=0;
    const cleanName=(value,kind)=>{
      if(typeof value!=='string'||!value.trim()||value.length>220)throw new Error('Invalid '+kind+' name.');
      return value.trim();
    };
    const teams=payload.teams.map((team,ti)=>{
      const name=cleanName(team?.name,'team');
      if(!Array.isArray(team.athletes)||team.athletes.length>500)throw new Error('Invalid athletes list in '+name+'.');
      return {id:'preview-team-'+ti,name,athletes:team.athletes.map((athlete,ai)=>{
        athleteCount++;
        if(athleteCount>1000)throw new Error('Hierarchy exceeds the athlete preview limit.');
        const aName=cleanName(athlete?.name,'athlete');
        const recordings=[];
        for(const classification of ['baseline','testing']){
          const shelves=athlete[classification]??[];
          if(!Array.isArray(shelves))throw new Error('Invalid '+classification+' shelves for '+aName+'.');
          for(const shelf of shelves){
            recordingCount++;
            if(recordingCount>3000)throw new Error('Hierarchy exceeds the EEG shelf preview limit.');
            const source_name=cleanName(shelf?.name??shelf?.source_name,'EEG shelf');
            if(shelf.files!==undefined&&!Array.isArray(shelf.files))throw new Error('Invalid EEG file list.');
            const files=(shelf.files??[]).slice(0,25).map(file=>({
              name:cleanName(typeof file==='string'?file:file?.name,'file'),
              size_bytes:typeof file==='object'&&Number.isSafeInteger(file?.size_bytes)&&file.size_bytes>=0?file.size_bytes:0
            }));
            recordings.push({id:'preview-rec-'+recordingCount,classification,source_name,files,task:{status:'preview'}});
          }
        }
        return {id:'preview-athlete-'+ti+'-'+ai,name:aName,recordings};
      })};
    });
    return {teams};
  }
  function receiveLibraryTransfer(input){
    const preview=parseLibraryTransfer(input);
    state.preview=preview;state.target=null;
    renderLibrary();
    notify(`Received ${preview.teams.length} team(s) as a folder-layout preview. EEG files are not imported until the Repository is connected.`,'success');
    return {accepted:true,preview_only:true,team_count:preview.teams.length};
  }
  function installLibraryReceiver(){
    const panel=$('library-reception-panel');
    panel.addEventListener('dragover',e=>{
      if(!e.dataTransfer||![...e.dataTransfer.types].includes(LIBRARY_TRANSFER_MIME))return;
      e.preventDefault();e.dataTransfer.dropEffect='copy';panel.classList.add('library-drag-over');
    });
    panel.addEventListener('dragleave',e=>{if(!panel.contains(e.relatedTarget))panel.classList.remove('library-drag-over');});
    panel.addEventListener('drop',e=>{
      panel.classList.remove('library-drag-over');
      if(!e.dataTransfer||![...e.dataTransfer.types].includes(LIBRARY_TRANSFER_MIME))return;
      e.preventDefault();
      try{
        const data=e.dataTransfer.getData(LIBRARY_TRANSFER_MIME);
        if(data.length>2_000_000)throw new Error('Repository object is too large for a folder-layout preview.');
        receiveLibraryTransfer(data);
      }catch(err){notify('Repository hierarchy rejected: '+err.message,'error');}
    });
    // Entry point for a future same-page Repository integration. No Repository code is shipped.
    window.NeurobuddyLibraryReceiver=Object.freeze({mimeType:LIBRARY_TRANSFER_MIME,schemaVersion:LIBRARY_TRANSFER_SCHEMA,receive:receiveLibraryTransfer});
    window.addEventListener('neurobuddy:library-transfer',e=>{
      try{receiveLibraryTransfer(e.detail);}catch(err){notify('Repository hierarchy rejected: '+err.message,'error');}
    });
    $('clear-library-preview').addEventListener('click',()=>{state.preview=null;renderLibrary();notify('Layout preview cleared. The local dataset was not changed.');});
  }
  async function loadLibrary(){await window.NeurobuddyShelf.refresh();}
  function findAthlete(id){for(const t of state.library.teams){const a=t.athletes.find(x=>x.id===id);if(a)return {team:t,athlete:a};}return null;}
  function linkTarget(mode,id,name){
    if($('processing-mode').value!==mode){$('processing-mode').value=mode;}
    const valid=mode==='single'?!!window.NeurobuddyShelf.findShelf(id):mode==='athlete'?!!findAthlete(id):state.library.teams.some(t=>t.id===id);
    if(!valid){notify('That shelf, athlete or team is not in this Dataset Library.','error');return;}
    state.target={mode,id,name};renderTarget();
    notify(`${name} selected for ${mode==='athlete'?'longitudinal athlete comparison':mode==='team'?'team batch processing':'single EEG analysis'}.`,'success');
  }
  function renderTarget(){
    const mode=$('processing-mode').value,target=state.target;
    $('drop-title').textContent=mode==='single'?'Drop an EEG shelf here':mode==='athlete'?'Drop an Athlete here':'Drop a Team here';
    $('drop-details').textContent=mode==='single'?'Drag an uploaded EEG recording from the left.':mode==='athlete'?'Oddball uses 2 compatible baselines plus 1 testing EEG. Sleep, motor, and unspecified stay at 3 baselines plus 1 testing EEG.':'Batch compare eligible athletes, preserving separate baselines.';
    const selected=$('selected-target');selected.hidden=!target||target.mode!==mode;
    if(!selected.hidden){selected.innerHTML=`<strong>${html(target.name)}</strong> ${badge(mode==='single'?'Single EEG':mode==='athlete'?'Athlete longitudinal':'Team batch')} <button class="secondary-button small-btn" id="clear-target" type="button">Clear</button>`;}
    const shelf=mode==='single'&&target?window.NeurobuddyShelf.findShelf(target.id)?.shelf:null;
    $('run-processing').disabled=selected.hidden||state.busy||(mode==='single'&&!shelf?.ready);
  }
  // The original pipeline HTML is authoritative: never recreate scientific
  // visualizations or truncate its metrics in an in-page preview.
  function reportLink(id,path,label,primary=false){
    if(!path)return '';
    return `<a class="${primary?'primary-button':'secondary-button'} report-open" href="${artifactUrl(id,path)}" target="_blank" rel="noopener noreferrer">${html(label)}</a>`;
  }
  function reportDownload(id,path,label){
    if(!path)return '';
    return `<a class="secondary-button" href="${artifactUrl(id,path)}" download>${html(label)}</a>`;
  }
  function reportRow(id,path,label,description=''){
    if(!path)return '';
    return `<div class="report-link-row"><div><strong>${html(label)}</strong>${description?`<small>${html(description)}</small>`:''}</div>${reportLink(id,path,'Open HTML report')}</div>`;
  }
  function reportForJob(id,data){
    if(data.mode==='single'){
      return `<div class="eyebrow">SINGLE RECORDING · v0.7.1</div><h2>EEG Analysis</h2>
        <p class="helper">The complete scientific results, graphs, interactive brain player and warnings are in the pipeline's original HTML report.</p>
        ${data.task_warning?`<p class="warning-text">${html(data.task_warning)}</p>`:''}
        <div class="report-primary">${reportLink(id,data.entrypoint||'report.html','Open full EEG HTML report',true)}</div>
        <div class="report-links">${reportDownload(id,data.package,'Download complete ZIP')}${reportDownload(id,'report.json','Download result JSON')}</div>`;
    }
    if(data.mode==='team'){
      return `<div class="eyebrow">TEAM BATCH · v0.7.1</div><h2>Team EEG Analysis</h2>
        <p class="helper">Open each athlete's complete longitudinal HTML report. Athletes with incompatible or missing baseline files are reported separately.</p>
        <div class="report-links"><button type="button" class="danger-icon team-delete" data-delete-report="${html(id)}" title="Delete this team analysis">Delete team analysis</button></div>
        <div class="team-result-list">${(data.members||[]).map(m=>`<div class="batch-member"><strong>${html(m.athlete_name)}</strong>${badge(m.status)}${m.status==='completed'?reportLink(id,m.relative_report,'Open athlete HTML report'):`<small>${html(m.message||'Not eligible')}</small>`}<button type="button" class="danger-icon" data-delete-report="${html(id)}" data-delete-athlete="${html(m.athlete_id)}" title="Delete this athlete report">Delete</button></div>`).join('')}</div>`;
    }
    const individuals=data.individual||[];
    const baselines=individuals.filter(x=>x.classification==='baseline');
    const testing=individuals.filter(x=>x.classification==='testing');
    return `<div class="eyebrow">ATHLETE COMPARISON · v0.7.1</div><h2>Longitudinal EEG Analysis</h2>
      <p class="helper">The original athlete HTML report includes the baseline average, testing EEG, comparison statistics, Z-scores and interactive heatmap.</p>
      <div class="report-primary">${reportLink(id,data.entrypoint||'athlete_report.html','Open full longitudinal HTML report',true)}</div>
      <div class="report-link-list">
        ${testing.map((r,i)=>reportRow(id,r.entrypoint||'test/report.html',`Testing EEG report ${i+1}`,'Original single-recording HTML')).join('')}
        ${baselines.map((r,i)=>reportRow(id,r.entrypoint,`Baseline EEG report ${i+1}`,'Original single-recording HTML')).join('')}
      </div>
      <div class="report-links">${reportDownload(id,data.package,'Download athlete report ZIP')}${reportDownload(id,'comparative_analysis.csv','Download comparison CSV')}${reportDownload(id,'comparative_analysis.json','Download comparison JSON')}</div>`;
  }
  async function loadJobs(){
    const r=await api('/v2/jobs');state.jobs=r.jobs||[];
    $('job-count').textContent=state.jobs.filter(j=>j.status==='completed').length;
    $('job-list').innerHTML=state.jobs.map(j=>`<div class="job-entry"><div class="job-entry-head"><button type="button" data-job="${html(j.id)}" class="run-item ${state.selectedJob===j.id?'active':''}"><strong>${j.mode==='single'?'Single EEG':j.mode==='team'?'Team batch':'Athlete comparison'} · ${html(new Date(j.created_at).toLocaleString())}</strong>${badge(j.status)}<small>Job ${html(j.id.slice(0,8))}</small>${j.error?`<small class="error-text">${html(j.error)}</small>`:''}</button><button type="button" class="danger-icon" data-delete-report="${html(j.id)}" title="${j.mode==='team'?'Delete this team analysis':'Delete this report'}">${j.mode==='team'?'Delete team analysis':'Delete'}</button></div>${j.status==='completed'&&j.mode!=='team'?`<div class="job-report-link">${reportLink(j.id,j.mode==='single'?'report.html':'athlete_report.html','Open HTML report')}</div>`:j.status==='completed'?'<p class="helper job-report-link">Select to view athlete reports</p>':''}</div>`).join('')||'<div class="empty">No jobs yet. Process an EEG in Dataset Library.</div>';
    if(state.selectedJob&&state.jobs.some(j=>j.id===state.selectedJob))await loadReport(state.selectedJob);
  }
  async function loadReport(id){
    const job=await api('/v2/jobs/'+encodeURIComponent(id));state.selectedJob=id;
    if(job.status==='failed'||job.status==='interrupted'){$('report-content').innerHTML=`<h2>Job ${html(job.status)}</h2><p class="error-text">${html(job.error)}</p><button class="secondary-button" data-retry-job="${html(id)}">Retry</button>`;return;}
    if(job.status!=='completed'||!job.result){$('report-content').innerHTML=`<h2>${html(job.status)}…</h2><p>The v0.7.1 engine is processing this recording. Results are saved on the local server.</p>`;return;}
    $('report-content').innerHTML=reportForJob(id,job.result);
  }
  async function launchJob(){
    const target=state.target;if(!target)return;
    const body={mode:target.mode,target_id:target.id,task_type:target.mode==='single'?null:$('test-type').value,
      artifact_mode:'experimental',mains_frequency_hz:60};
    state.busy=true;renderTarget();
    try{const job=await api('/v2/shelf-jobs',jsonOpt('POST',body));state.selectedJob=job.id;
      notify('Processing queued with Neurobuddy v0.7.1. Results will appear under Analysis.','success');
      navigate('analysis');await loadJobs();await loadReport(job.id);
    }finally{state.busy=false;renderTarget();}
  }
  function shelfFileName(shelf){
    const name=String(shelf.source_name||'');
    const cut=Math.max(name.lastIndexOf('/'),name.lastIndexOf('\\'));
    return cut>=0?name.slice(cut+1):name;
  }
  function advancedShelfLabel(shelf){
    const kind=shelf.classification==='baseline'?'Baseline':'Testing';
    const file=shelfFileName(shelf);
    return `${kind} · ${shelf.task_type||'unspecified'}${file?` · ${file}`:' · No EEG file yet'}`;
  }
  function findAdvancedShelf(id){
    for(const team of state.advanced.teams||[])for(const athlete of team.athletes||[]){
      const shelf=(athlete.shelves||[]).find(item=>item.id===id);
      if(shelf)return {team,athlete,shelf};
    }
    return null;
  }
  function aiReportChips(athlete){
    const runs=(athlete.reports||[]).filter(r=>(r.html_reports||[]).length);
    if(!runs.length)return '';
    return `<div class="ai-report-list"><div class="ai-report-heading">Longitudinal reports · drag into AI processing</div>${runs.map(r=>{
      const when=new Date(r.created_at).toLocaleString();
      const kind=r.mode==='team'?'Team batch':'Athlete comparison';
      return `<div class="ai-report-run"><small>${html(kind)} · ${html(when)}</small><div class="actions">${r.html_reports.map(h=>{
        const active=state.aiReport&&state.aiReport.kind==='job'&&state.aiReport.job_id===h.job_id&&state.aiReport.path===h.path;
        return `<button type="button" class="secondary-button small-btn ai-report-chip${active?' active':''}" draggable="true" data-ai-job="${html(h.job_id)}" data-ai-path="${html(h.path)}" data-ai-label="${html(athlete.name+' · '+h.label+' · '+when)}" title="Drag this longitudinal report into the AI processing area, or click to select it">⧉ ${html(h.label)}</button>`;}).join('')}</div></div>`;}).join('')}</div>`;
  }
  // Athletes whose latest runs include a longitudinal report. These are what a team selection sends to the AI.
  function longitudinalAthletes(team){
    return (team.athletes||[]).filter(a=>(a.reports||[]).some(r=>(r.html_reports||[]).length));
  }
  async function loadAdvanced(){
    safe(loadAIStatus);
    state.advanced=await api('/v2/shelf-advanced');
    $('advanced-tree').innerHTML=state.advanced.teams.map(t=>`<details open class="folder"><summary>▱ ${html(t.name)} <span class="small-count">${t.athletes.length} athlete(s)</span></summary><div class="folder-inner">
      <div class="actions"><button class="secondary-button small-btn" draggable="true" data-advanced-type="team" data-advanced-id="${html(t.id)}" title="Sends the latest longitudinal report of every athlete in this team">↗ Select / Drag Team results · ${longitudinalAthletes(t).length} longitudinal report(s)</button><button type="button" class="danger-icon" data-delete-advanced-team="${html(t.id)}" title="Delete this team">Delete team</button></div>
      ${t.athletes.map(a=>`<article class="recording"><div class="recording-top"><strong>♙ ${html(a.name)}</strong>${badge('completed')}</div><small>${a.reports.length} saved run(s)</small><div class="actions"><button type="button" class="danger-icon" data-delete-advanced-athlete="${html(a.id)}" title="Delete this athlete">Delete athlete</button></div>
        ${aiReportChips(a)}
        <div class="advanced-shelves">${(a.shelves||[]).map(s=>`<div class="advanced-shelf"><span>${html(advancedShelfLabel(s))}</span><button type="button" class="danger-icon" data-delete-shelf="${html(s.id)}" title="Delete this EEG shelf">Delete shelf</button></div>`).join('')||'<p class="helper">No EEG shelves for this athlete.</p>'}</div></article>`).join('')}
      </div></details>`).join('')||'<div class="empty">Completed EEG analyses will appear here by Team and Athlete.</div>';
  }
  function findAdvancedAthlete(id){
    for(const team of state.advanced.teams||[]){
      const athlete=(team.athletes||[]).find(item=>item.id===id);
      if(athlete)return {team,athlete};
    }
    return null;
  }
  function clearAdvancedSelection(id){
    const selected=state.advancedSelection;
    if(!selected)return;
    if(selected.team&&selected.team.id===id)state.advancedSelection=null;
    if((selected.athletes||[]).some(item=>item.id===id))state.advancedSelection=null;
    if(!state.advancedSelection){$('advanced-selection').textContent='';$('preview-package').disabled=true;$('download-package').disabled=true;$('package-json').hidden=true;}
  }
  async function deleteAdvancedShelf(id){
    const found=findAdvancedShelf(id);
    const label=found?advancedShelfLabel(found.shelf):'this EEG shelf';
    if(!window.confirm(`Delete ${label}? This removes that shelf and its recording files. Analysis reports, other athletes' shelves, and the team stay.`))return;
    await api(`/v2/shelves/${encodeURIComponent(id)}`,{method:'DELETE'});
    if(state.target&&state.target.id===id)state.target=null;
    await loadAdvanced();
    if(window.NeurobuddyShelf)await window.NeurobuddyShelf.refresh();
    notify('EEG shelf deleted. Reports and other shelves stay.','success');
  }
  async function deleteAdvancedAthlete(id){
    const found=findAdvancedAthlete(id);
    const name=found?found.athlete.name:'this athlete';
    if(!window.confirm(`Delete ${name}? This removes that athlete and that athlete's EEG shelves and recording files. Other athletes, the team, and analysis reports stay.`))return;
    await api(`/v2/shelf-athletes/${encodeURIComponent(id)}`,{method:'DELETE'});
    if(state.target&&state.target.id===id)state.target=null;
    clearAdvancedSelection(id);
    await loadAdvanced();
    if(window.NeurobuddyShelf)await window.NeurobuddyShelf.refresh();
    notify('Athlete deleted. The team, other athletes, and reports stay.','success');
  }
  async function deleteAdvancedTeam(id){
    const team=(state.advanced.teams||[]).find(item=>item.id===id);
    const name=team?team.name:'this team';
    if(!window.confirm(`Delete ${name}? This removes that team, its athletes, and those athletes' EEG shelves and recording files. Other teams and analysis reports stay.`))return;
    await api(`/v2/shelf-teams/${encodeURIComponent(id)}`,{method:'DELETE'});
    if(state.target&&state.target.id===id)state.target=null;
    clearAdvancedSelection(id);
    await loadAdvanced();
    if(window.NeurobuddyShelf)await window.NeurobuddyShelf.refresh();
    notify('Team deleted. Other teams and reports stay.','success');
  }
  async function chooseAdvanced(type,id){
    const team=state.advanced.teams.find(t=>t.id===id||t.athletes.some(a=>a.id===id));
    if(!team)throw new Error('Select a completed analysis package.');
    const athletes=type==='team'?team.athletes:team.athletes.filter(a=>a.id===id);
    const data=[];
    for(const a of athletes){
      const reports=[];
      for(const r of a.reports){
        const job=await api('/v2/jobs/'+encodeURIComponent(r.id));
        if(!job.result)continue;
        let payload=job.result;
        if(payload.mode==='team'){
          const member=(payload.members||[]).find(m=>m.athlete_id===a.id&&m.status==='completed');
          if(!member)continue;
          payload=member.result;
        }
        reports.push({job_id:r.id,created_at:r.created_at,mode:payload.mode,pipeline_version:'0.7.1',
          ...(payload.mode==='single'?{analysis:payload.analysis}:{baseline:payload.baseline,
             comparison:payload.comparison,individual_analyses:payload.individual.map(x=>({classification:x.classification,analysis:x.result}))})});
      }
      data.push({id:a.id,name:a.name,reports});
    }
    state.advancedSelection={schema_version:'neurobuddy.coach-analysis-package/0.1',type,
      generated_at:new Date().toISOString(),source_pipeline:'Neurobuddy v0.7.1',ai_processed:false,
      team:{id:team.id,name:team.name},athletes:data};
    $('advanced-selection').textContent=`${type==='team'?'Team':'Athlete'} results prepared locally. No AI backend or external service was called.`;
    $('preview-package').disabled=false;$('download-package').disabled=false;$('package-json').hidden=true;
  }
  function downloadJSON(obj,name){const blob=new Blob([JSON.stringify(obj,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function attachDrop(el,type,callback){
    el.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes(type)){e.preventDefault();el.classList.add('drag-over');}});
    el.addEventListener('dragleave',()=>el.classList.remove('drag-over'));
    el.addEventListener('drop',e=>{el.classList.remove('drag-over');const data=e.dataTransfer.getData(type);if(data){e.preventDefault();try{callback(JSON.parse(data));}catch(err){notify(err.message,'error');}}});
  }
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.tab)));
  window.NeurobuddyShelf.install({api,safe,notify,state,linkTarget,renderTarget});
  function showWalkthrough(){
    const player=$('walkthrough-player');
    const label=$('walkthrough-label');
    if(!player)return;
    const next=['v2/videos/all-tests.mp4','Oddball, motor imagery, then sleep'];
    const playing=!player.paused&&!player.ended;
    const file=next[0].slice(next[0].lastIndexOf('/')+1);
    if(!player.currentSrc.endsWith(file)){
      player.src=next[0];
      if(playing)player.play().catch(()=>{});
    }
    if(label)label.textContent=next[1];
  }
  $('test-type').addEventListener('change',showWalkthrough);
  showWalkthrough();
  $('processing-mode').addEventListener('change',()=>{state.target=null;renderTarget();});
  $('selected-target').addEventListener('click',e=>{if(e.target.id==='clear-target'){state.target=null;renderTarget();}});
  attachDrop($('processing-drop'),'application/x-neurobuddy-source',item=>linkTarget(item.type,item.id,item.name));
  $('run-processing').addEventListener('click',()=>safe(launchJob));
  $('refresh-jobs').addEventListener('click',()=>safe(loadJobs));
  async function deleteReport(id,athleteId){
    const job=state.jobs.find(item=>item.id===id);
    if(job&&['queued','running'].includes(job.status)){
      notify('Wait until this analysis finishes before deleting it.','error');
      return;
    }
    const scope=athleteId?'this athlete report':job&&job.mode==='team'?'this team analysis and every athlete report inside it':'this analysis report';
    if(!window.confirm(`Delete ${scope}? It leaves the list and its saved report files are removed. Other reports and EEG shelf files stay.`))return;
    const query=athleteId?`?athlete_id=${encodeURIComponent(athleteId)}`:'';
    const result=await api(`/v2/jobs/${encodeURIComponent(id)}${query}`,{method:'DELETE'});
    if(result.removed==='job'&&state.selectedJob===id){
      state.selectedJob=null;
      $('report-content').innerHTML='<div class="empty">Select a completed analysis to open its original interactive HTML report.</div>';
    }
    await loadJobs();
    if(result.removed==='athlete'&&state.selectedJob===id)await loadReport(id);
    notify('Report deleted.','success');
  }
  $('job-list').addEventListener('click',e=>safe(async()=>{const del=e.target.closest('[data-delete-report]');if(del){await deleteReport(del.dataset.deleteReport,del.dataset.deleteAthlete||null);return;}const b=e.target.closest('[data-job]');if(b){state.selectedJob=b.dataset.job;await loadJobs();}}));
  $('report-content').addEventListener('click',e=>safe(async()=>{const del=e.target.closest('[data-delete-report]');if(del){await deleteReport(del.dataset.deleteReport,del.dataset.deleteAthlete||null);return;}const b=e.target.closest('[data-retry-job]');if(b){const r=await api('/v2/jobs/'+encodeURIComponent(b.dataset.retryJob)+'/retry',{method:'POST'});state.selectedJob=r.id;await loadJobs();}}));
  $('advanced-tree').addEventListener('click',e=>{
    const shelf=e.target.closest('[data-delete-shelf]');
    if(shelf){safe(()=>deleteAdvancedShelf(shelf.dataset.deleteShelf));return;}
    const athlete=e.target.closest('[data-delete-advanced-athlete]');
    if(athlete){safe(()=>deleteAdvancedAthlete(athlete.dataset.deleteAdvancedAthlete));return;}
    const team=e.target.closest('[data-delete-advanced-team]');
    if(team){safe(()=>deleteAdvancedTeam(team.dataset.deleteAdvancedTeam));return;}
    const report=e.target.closest('[data-ai-path]');
    if(report){safe(()=>selectAIReport({kind:'job',job_id:report.dataset.aiJob,path:report.dataset.aiPath,label:report.dataset.aiLabel}));return;}
    const b=e.target.closest('[data-advanced-type]');
    if(b){safe(()=>selectAISubject(b.dataset.advancedType,b.dataset.advancedId));}
  });
  $('advanced-tree').addEventListener('dragstart',e=>{const r=e.target.closest('[data-ai-path]');if(r){e.dataTransfer.effectAllowed='copy';e.dataTransfer.setData('application/x-neurobuddy-report',JSON.stringify({job_id:r.dataset.aiJob,path:r.dataset.aiPath,label:r.dataset.aiLabel}));return;}const b=e.target.closest('[data-advanced-type]');if(b){e.dataTransfer.effectAllowed='copy';e.dataTransfer.setData('application/x-neurobuddy-advanced',JSON.stringify({type:b.dataset.advancedType,id:b.dataset.advancedId}));}});
  // ---------------------------------------------------------------- AI report analysis
  const REPORT_MIME='application/x-neurobuddy-report';
  async function loadAIStatus(){
    state.aiStatus=await api('/v2/ai-analysis/status');
    const chip=$('ai-status-chip');
    chip.textContent=state.aiStatus.configured?`AI backend: Connected (${state.aiStatus.endpoint_host})`:'AI backend: Not configured';
    chip.classList.toggle('connected',state.aiStatus.configured);
    renderAIControls();
  }
  function renderAIControls(){
    const button=$('process-ai'),help=$('ai-help');
    const configured=Boolean(state.aiStatus&&state.aiStatus.configured);
    button.disabled=state.aiBusy||!state.aiReport||!configured;
    button.textContent=state.aiBusy?'Analyzing report…':configured?'Process with AI':'Process with AI · Not configured';
    button.title=!configured?'Set NEUROBUDDY_AI_API_URL on the computer running Coach, then restart it.':!state.aiReport?'Drop a team or a longitudinal report first':'Send this selection to the AI for analysis';
    if(state.aiStatus&&!configured)help.innerHTML='The AI analysis API is not connected yet. When FloLabs supplies it, set <code>NEUROBUDDY_AI_API_URL</code> (and <code>NEUROBUDDY_AI_API_KEY</code>) in the terminal that starts Coach, then restart it. You can still select reports and prepare JSON packages.';
    else help.textContent=`Drop a team to send the latest longitudinal report of every athlete in it, or drop one longitudinal report. A longitudinal report compares the testing session with the athlete's baselines. The AI analysis API (${state.aiStatus?.endpoint_host||'external'}) lists the metrics outside and within ±3 SD of baseline with their z-scores and what they may be related to (recovery, fatigue, sleep, stress, attention). Results are exploratory, not a diagnosis.`;
  }
  async function selectAIReport(report){
    state.aiReport=report;state.advancedSelection=null;
    $('preview-package').disabled=true;$('download-package').disabled=true;$('package-json').hidden=true;
    $('advanced-selection').textContent=`Selected HTML report: ${report.label}. Press Process with AI to analyze it.`;
    document.querySelectorAll('.ai-report-chip').forEach(b=>b.classList.toggle('active',report.kind==='job'&&b.dataset.aiJob===report.job_id&&b.dataset.aiPath===report.path));
    renderAIControls();
    $('ai-result-panel').hidden=true;
    if(report.kind==='job'){
      try{const saved=await api(`/v2/ai-analysis/report?job_id=${encodeURIComponent(report.job_id)}&path=${encodeURIComponent(report.path)}`);
        if(state.aiReport===report&&saved.saved)showAIResult(saved,true);}
      catch{/* no saved analysis yet */}
    }
  }
  async function selectAISubject(type,id){
    const team=state.advanced.teams.find(t=>t.id===id||t.athletes.some(a=>a.id===id));
    const athlete=type==='athlete'&&team?team.athletes.find(a=>a.id===id):null;
    const members=team?longitudinalAthletes(team):[];
    const label=type==='team'?`${team?team.name:'Team'} (team, ${members.length} athlete(s))`:`${athlete?athlete.name:'Athlete'} (athlete)`;
    const subject={kind:'subject',type,id,label};
    state.aiReport=subject;
    document.querySelectorAll('.ai-report-chip').forEach(b=>b.classList.remove('active'));
    renderAIControls();
    $('ai-result-panel').hidden=true;
    await chooseAdvanced(type,id);
    $('advanced-selection').textContent=type==='team'
      ?`Selected ${label}. The latest longitudinal report of each athlete (${members.map(a=>a.name).join(', ')||'none yet'}) is sent to the AI, one analysis per athlete. Press Process with AI to analyze.`
      :`Selected ${label}: every baseline statistic and the testing session compared against it. Press Process with AI to analyze.`;
    try{const saved=await api(`/v2/ai-analysis/subject?type=${encodeURIComponent(type)}&id=${encodeURIComponent(id)}`);
      if(state.aiReport===subject&&saved.saved)showAIResult(saved,true);}
    catch{/* no saved analysis yet */}
  }
  function selectAIFile(file){
    if(!file)return;
    if(!/\.html?$/i.test(file.name)){notify('Drop a Neurobuddy HTML report (.html file).','error');return;}
    selectAIReport({kind:'file',file,label:`${file.name} (${fmtSize(file.size)}) from this computer`});
  }
  function inlineMarkdown(text){
    return html(text).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/(^|[^*])\*([^*\s][^*]*?)\*(?!\*)/g,'$1<em>$2</em>');
  }
  function renderMarkdown(md){
    const lines=String(md||'').replace(/\r/g,'').split('\n');const out=[];let list=null,para=[];
    const flush=()=>{if(para.length){out.push(`<p>${inlineMarkdown(para.join(' '))}</p>`);para=[];}if(list){out.push(`</${list}>`);list=null;}};
    for(let i=0;i<lines.length;i++){
      const line=lines[i];
      if(/^\s*\|.*\|\s*$/.test(line)&&i+1<lines.length&&/^\s*\|?\s*:?-{2,}/.test(lines[i+1])){
        flush();const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
        const head=cells(line);i+=2;const rows=[];
        while(i<lines.length&&/^\s*\|.*\|\s*$/.test(lines[i])){rows.push(cells(lines[i]));i++;}i--;
        out.push(`<div class="table-scroll"><table class="metrics-table"><thead><tr>${head.map(c=>`<th>${inlineMarkdown(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${inlineMarkdown(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      const quote=line.match(/^\s*>\s?(.*)$/);
      if(quote){flush();out.push(`<blockquote>${inlineMarkdown(quote[1])}</blockquote>`);continue;}
      const h=line.match(/^(#{1,6})\s+(.*)$/);
      if(h){flush();const level=Math.min(6,h[1].length+1);out.push(`<h${level}>${inlineMarkdown(h[2])}</h${level}>`);continue;}
      const bullet=line.match(/^\s*[-*]\s+(.*)$/),num=line.match(/^\s*\d+[.)]\s+(.*)$/);
      if(bullet||num){if(para.length){out.push(`<p>${inlineMarkdown(para.join(' '))}</p>`);para=[];}const tag=bullet?'ul':'ol';if(list!==tag){if(list)out.push(`</${list}>`);out.push(`<${tag}>`);list=tag;}out.push(`<li>${inlineMarkdown((bullet||num)[1])}</li>`);continue;}
      if(!line.trim()){flush();continue;}
      if(list){out.push(`</${list}>`);list=null;}
      para.push(line.trim());
    }
    flush();return out.join('');
  }
  // Put the "Outside ±3 SD" and "Within ±3 SD" sections next to each other.
  function pairSideBySide(root){
    const headings=[...root.querySelectorAll('h2,h3,h4')];
    const section=h=>{const level=Number(h.tagName[1]);const nodes=[h];let n=h.nextElementSibling;
      while(n&&!(/^H[1-6]$/.test(n.tagName)&&Number(n.tagName[1])<=level)){nodes.push(n);n=n.nextElementSibling;}return nodes;};
    headings.filter(h=>/^Outside\s*±\s*3\s*SD/i.test(h.textContent)).forEach(h=>{
      const outside=section(h),last=outside[outside.length-1],next=last.nextElementSibling;
      if(!next||!/^Within\s*±\s*3\s*SD/i.test(next.textContent)||next.tagName!==h.tagName)return;
      const within=section(next),grid=document.createElement('div');grid.className='ai-columns';
      h.before(grid);
      for(const [nodes,cls] of [[outside,'ai-column outside'],[within,'ai-column within']]){
        const col=document.createElement('div');col.className=cls;nodes.forEach(x=>col.appendChild(x));grid.appendChild(col);}
    });
  }
  // Colour the "Range" cells: out of range in red, in range in green.
  function markRanges(root){
    root.querySelectorAll('td').forEach(td=>{const t=td.textContent.trim();
      if(/^Out of range/i.test(t))td.classList.add('range-out');else if(/^In range/i.test(t))td.classList.add('range-in');});
  }
  const AI_REPORT_CSS=`*{box-sizing:border-box}body{margin:0;background:#f5f6fa;color:#1c2333;font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:40px 28px 60px}header.top{border-bottom:3px solid #5b4bdb;padding-bottom:18px;margin-bottom:8px}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:#5b4bdb;font-weight:700}h1{font-size:30px;margin:6px 0 4px}
.meta{color:#5a6478;font-size:13px}.disclaimer{background:#fff6e8;border-left:4px solid #e3a23c;padding:10px 14px;border-radius:6px;font-size:13px;margin:18px 0}
h2{font-size:21px;margin:34px 0 10px;padding:10px 14px;background:#ecebfb;border-radius:8px;color:#2d2470}h3{font-size:16px;margin:20px 0 8px;color:#2d2470}h4{font-size:14px;margin:16px 0 6px}
.table-scroll{overflow-x:auto;margin:8px 0 14px;border:1px solid #dfe2ea;border-radius:8px;background:#fff}table{border-collapse:collapse;width:100%;font-size:13px}
th{background:#f0f1f6;text-align:left;font-weight:700;padding:8px 10px;border-bottom:1px solid #dfe2ea}td{padding:8px 10px;border-bottom:1px solid #eef0f4;vertical-align:top}
tr:last-child td{border-bottom:0}td.range-out{color:#b3261e;font-weight:700;background:#fdecea;white-space:nowrap}td.range-in{color:#1b7a52;font-weight:600;background:#eaf7f1;white-space:nowrap}
ul,ol{padding-left:22px}li{margin:4px 0}code{background:#eef0f6;padding:1px 5px;border-radius:4px;font-size:12px}blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid #c9cde0;color:#4a5266}
footer{margin-top:40px;color:#6b7386;font-size:12px;border-top:1px solid #dfe2ea;padding-top:12px}@media print{body{background:#fff}h2{break-after:avoid}tr{break-inside:avoid}}`;
  function aiReportHTML(r){
    const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const body=document.createElement('div');body.innerHTML=renderMarkdown(r.analysis_markdown);markRanges(body);
    const when=r.created_at?new Date(r.created_at).toLocaleString():'';
    return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Neuro-Buddy AI analysis · ${esc(r.source?.label||'report')}</title><style>${AI_REPORT_CSS}</style></head><body><main>
<header class="top"><div class="eyebrow">Neuro-Buddy · AI interpretation · exploratory</div><h1>AI analysis · ${esc(r.source?.label||'EEG report')}</h1><div class="meta">${esc(when)} · Model ${esc(r.model||'')}</div></header>
<div class="disclaimer">${esc(r.disclaimer||'Exploratory AI interpretation. Not a diagnosis or a readiness decision.')} Each value is compared with this athlete's own baseline: "Out of range" means more than 3 standard deviations from the baseline mean.</div>
${body.innerHTML}
<footer>Generated by Neuro-Buddy Coach from the pipeline's own statistics. It can be wrong, it is not a medical or sleep diagnosis, and it is not a training-readiness decision. Review it with a qualified professional.</footer></main></body></html>`;
  }
  function showAIResult(result,saved=false){
    state.aiResult=result;
    $('ai-result-title').textContent=`AI analysis · ${result.source?.label||'HTML report'}`;
    const when=result.created_at?new Date(result.created_at).toLocaleString():'';
    const failed=(result.athletes_failed||[]).length;
    const sent=result.athletes_sent!=null?`${result.athletes_sent} longitudinal report(s) sent${failed?`, ${failed} not analyzed`:''}`:`${result.figures_sent??0} figure(s) sent`;
    $('ai-result-meta').textContent=`${saved?'Saved analysis':'New analysis'} · ${result.model||''} · ${when} · ${sent}${result.truncated?' · Output was cut off at the length limit':''}`;
    $('ai-result').innerHTML=renderMarkdown(result.analysis_markdown);
    pairSideBySide($('ai-result'));
    markRanges($('ai-result'));
    $('ai-result-panel').hidden=false;
  }
  async function runAI(){
    const report=state.aiReport;
    if(!report||state.aiBusy)return;
    state.aiBusy=true;renderAIControls();
    $('advanced-selection').textContent=`Analyzing ${report.label}. This usually takes one to three minutes${report.type==='team'?' (athletes are analyzed side by side)':''}.`;
    try{
      let result;
      if(report.kind==='job')result=await api('/v2/ai-analysis/report',jsonOpt('POST',{job_id:report.job_id,path:report.path}));
      else if(report.kind==='subject')result=await api('/v2/ai-analysis/subject',jsonOpt('POST',{type:report.type,id:report.id}));
      else{const form=new FormData();form.append('file',report.file,report.file.name);result=await api('/v2/ai-analysis/upload',{method:'POST',body:form});}
      showAIResult(result);
      $('advanced-selection').textContent=`AI analysis complete for ${report.label}.`;
      notify('AI analysis complete. It is exploratory and not a diagnosis.','success');
      $('ai-result-panel').scrollIntoView({behavior:'smooth',block:'start'});
    }catch(e){
      $('advanced-selection').textContent=`AI analysis failed: ${e.message}`;
      throw e;
    }finally{state.aiBusy=false;renderAIControls();}
  }
  const advancedDrop=$('advanced-drop');
  advancedDrop.addEventListener('dragover',e=>{const types=[...e.dataTransfer.types];if(types.includes('application/x-neurobuddy-advanced')||types.includes(REPORT_MIME)||types.includes('Files')){e.preventDefault();advancedDrop.classList.add('drag-over');}});
  advancedDrop.addEventListener('dragleave',()=>advancedDrop.classList.remove('drag-over'));
  advancedDrop.addEventListener('drop',e=>{
    advancedDrop.classList.remove('drag-over');
    const report=e.dataTransfer.getData(REPORT_MIME),pkg=e.dataTransfer.getData('application/x-neurobuddy-advanced');
    try{
      if(report){e.preventDefault();const r=JSON.parse(report);safe(()=>selectAIReport({kind:'job',job_id:r.job_id,path:r.path,label:r.label}));return;}
      if(pkg){e.preventDefault();const item=JSON.parse(pkg);safe(()=>selectAISubject(item.type,item.id));return;}
      if(e.dataTransfer.files&&e.dataTransfer.files.length){e.preventDefault();selectAIFile(e.dataTransfer.files[0]);}
    }catch(err){notify(err.message,'error');}
  });
  $('choose-ai-report').addEventListener('click',e=>{e.stopPropagation();$('ai-report-file').value='';$('ai-report-file').click();});
  $('ai-report-file').addEventListener('change',e=>selectAIFile(e.target.files&&e.target.files[0]));
  $('process-ai').addEventListener('click',()=>safe(runAI));
  $('download-ai-html').addEventListener('click',()=>{
    const r=state.aiResult;if(!r)return;
    const name=String(r.source?.label||'report').replace(/[^A-Za-z0-9._-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60)||'report';
    const blob=new Blob([aiReportHTML(r)],{type:'text/html'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download=`neurobuddy-ai-analysis-${name}.html`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('download-ai-result').addEventListener('click',()=>{
    const r=state.aiResult;if(!r)return;
    const header=`# AI report analysis\n\nSource: ${r.source?.label||''}\nModel: ${r.model||''}\nCreated: ${r.created_at||''}\n\n> ${r.disclaimer||''}\n\n`;
    const blob=new Blob([header+r.analysis_markdown],{type:'text/markdown'}),url=URL.createObjectURL(blob),a=document.createElement('a');
    a.href=url;a.download='neurobuddy-ai-analysis.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  $('preview-package').addEventListener('click',()=>{if(state.advancedSelection){$('package-json').textContent=JSON.stringify(state.advancedSelection,null,2);$('package-json').hidden=false;}});
  $('download-package').addEventListener('click',()=>{if(state.advancedSelection)downloadJSON(state.advancedSelection,`neurobuddy-${state.advancedSelection.type}-analysis.json`);});
  window.addEventListener('hashchange',()=>navigate(['library','analysis','advanced'].includes(location.hash.slice(1))?location.hash.slice(1):'library'));
  setInterval(()=>safe(async()=>{try{await api('/v2/health');$('online-dot').classList.remove('off');$('server-status').textContent='Local server connected';}catch{$('online-dot').classList.add('off');$('server-status').textContent='Server disconnected';}}),15000);
  setInterval(()=>{if(state.tab==='analysis'&&state.jobs.some(j=>['queued','running'].includes(j.status)))safe(loadJobs);},2500);
  safe(async()=>{await api('/v2/health');$('online-dot').classList.remove('off');$('server-status').textContent='Local server connected';await loadLibrary();await loadJobs();navigate(['library','analysis','advanced'].includes(location.hash.slice(1))?location.hash.slice(1):'library');});
})();
