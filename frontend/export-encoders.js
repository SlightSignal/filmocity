(function (root, factory) {
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else root.FilmocityExportEncoders = value;
})(typeof window === 'undefined' ? globalThis : window, function () {
  'use strict';
  const family = format => format === 'h264mov' ? 'h264' : ['h264', 'hevc'].includes(format) ? format : null;
  function choices(details, format, desired, explicit = false) {
    const kind = family(format);
    if (!kind) return { disabled: true, selected: '', options: [{ id: '', label: 'Determined by output format' }], hint: '' };
    const options = details.filter(e => e.format === kind && e.listed).map(e => ({ ...e }));
    let selected = desired;
    if (!options.some(e => e.id === selected)) {
      if (explicit && desired) options.unshift({ id: desired, label: `Unavailable for this format: ${desired}`, disabled: true });
      else {
        const partner = desired?.replace(/^(h264|hevc)_/, kind + '_');
        selected = options.find(e => e.id === partner)?.id || options.find(e => !e.hardware)?.id || options[0]?.id || '';
      }
    }
    if (!options.length) options.push({ id: '', label: `No ${kind.toUpperCase()} encoder found`, disabled: true });
    const chosen = options.find(e => e.id === selected);
    return { disabled: false, selected, options,
      hint: chosen?.disabled ? 'Choose an available encoder before exporting.' : chosen?.hardware
        ? 'Uses the selected bitrate, or 10 Mbps for a CRF quality preset. Export checks this device before starting.'
        : 'Export checks the selected encoder on this device before starting.' };
  }
  return { family, choices };
});
