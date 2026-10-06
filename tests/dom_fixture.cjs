// A controlled DOM fixture, not a browser accessibility/visual acceptance test.
class Element {
  constructor(tag, document) { this.tagName = tag; this.document = document; this.children = []; this.events = {}; this.attributes = {}; this.dataset = {}; this.value = ''; this.disabled = false; this.open = false; this.isConnected = true; this.textContent = ''; }
  appendChild(child) { this.children.push(child); child.parentElement = this; return child; }
  replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, fn) { this.events[name] = fn; }
  focus() { this.document.activeElement = this; this.focusCount = (this.focusCount || 0) + 1; }
  select() { this.selectedText = true; }
  scrollIntoView() { this.scrolled = true; }
  showModal() { this.open = true; }
  close() { this.open = false; /* Native close is queued; tests can dispatch it later. */ }
}
function fixture(ids) {
  const nodes = {};
  const document = { activeElement: null, otherDialog: false, getElementById: id => nodes[id], createElement: tag => new Element(tag, document),
    querySelector: () => document.otherDialog ? {} : Object.values(nodes).find(node => node.open) || null,
  };
  for (const id of ids) { nodes[id] = new Element('div', document); nodes[id].id = id; }
  const origin = new Element('button', document); origin.focus();
  return { document, nodes, origin };
}
function key(key, extra = {}) {
  return { key, code: '', ctrlKey: false, altKey: false, shiftKey: false, metaKey: false, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.stopped = true; }, ...extra };
}
function deferred() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; }
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
module.exports = { Element, fixture, key, deferred, settle };
