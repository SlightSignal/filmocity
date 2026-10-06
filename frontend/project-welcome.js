(function (global) {
  function renderWelcome({ document, host, hasMedia, filtered, importFiles, browse, findCommands, clearFilters }) {
    const section = document.createElement('section'); section.className = 'project-welcome';
    const eyebrow = document.createElement('p'); eyebrow.className = 'eyebrow'; eyebrow.textContent = hasMedia ? 'PROJECT MEDIA' : 'START YOUR EDIT';
    const title = document.createElement('h3'); title.textContent = hasMedia ? (filtered ? 'No matching media' : 'This bin is empty') : 'Bring your footage in';
    const detail = document.createElement('p'); detail.textContent = hasMedia ? 'Clear the search and folder filter to see all imported media.' : 'Add video, audio, or images. Your first sequence is ready below.';
    const actions = document.createElement('div'); actions.className = 'welcome-actions';
    function button(label, action, primary = false) { const node = document.createElement('button'); node.type = 'button'; node.textContent = label; if (primary) node.className = 'primary'; node.onclick = action; actions.appendChild(node); }
    if (hasMedia) button('Show all media', clearFilters, true);
    else { button('Import files', importFiles, true); button('Browse folders', browse); }
    button('Find a command', findCommands);
    section.appendChild(eyebrow); section.appendChild(title); section.appendChild(detail); section.appendChild(actions);
    host.replaceChildren(section);
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { renderWelcome };
  else global.FilmocityWelcome = { renderWelcome };
})(typeof window === 'undefined' ? globalThis : window);
