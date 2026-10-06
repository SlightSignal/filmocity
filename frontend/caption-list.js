/* Preserve original sorted indices while mounting only one page of captions. */
(function(global){
  function page(captions, state, size=30){
    const query=String(state.query||'').normalize('NFKD').replace(/\p{M}/gu,'').toLocaleLowerCase();
    const matches=captions.map((caption,index)=>({caption,index})).filter(({caption})=>String(caption.text).normalize('NFKD').replace(/\p{M}/gu,'').toLocaleLowerCase().includes(query));
    const pages=Math.max(1,Math.ceil(matches.length/size));state.page=Math.max(0,Math.min(pages-1,state.page||0));
    return {items:matches.slice(state.page*size,(state.page+1)*size),pages,total:matches.length};
  }
  global.FilmocityCaptionList={page};if(typeof module!=='undefined')module.exports={page};
})(typeof window==='undefined'?globalThis:window);
