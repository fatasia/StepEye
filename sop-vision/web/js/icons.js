const P = {
  overview: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
  live: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10l6-3v10l-6-3z"/>',
  trace: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/>',
  config: '<path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>',
  running: '<circle cx="12" cy="12" r="8.5"/><path d="M10.2 8.6l5 3.4-5 3.4z" fill="currentColor" stroke="none"/>',
  idle: '<circle cx="12" cy="12" r="8"/>',
  warn: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.4v.4"/>',
  alarm: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6.2M12 16.6v.4"/>',
  offline: '<path d="M2.5 9a15 15 0 0 1 4-2.4M21.5 9a15 15 0 0 0-8-3.6M5.5 13a10 10 0 0 1 3.4-2.2M18.5 13a10 10 0 0 0-3.6-2.3M9 16.5a4.5 4.5 0 0 1 6 0M12 20h.01M3 3l18 18"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  cross: '<path d="M6 6l12 12M18 6L6 18"/>',
  question: '<path d="M9.3 9.2a2.8 2.8 0 1 1 4.4 2.3c-1 .7-1.7 1.3-1.7 2.5M12 17.4v.4"/>',
  order: '<path d="M4 7h9a4 4 0 0 1 4 4v5M14 13.5l3 3 3-3"/>',
  active: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 8 8" stroke-width="2.6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/>',
  refresh: '<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
  palette: '<path d="M12 3a9 9 0 1 0 0 18c1.5 0 2-1 1.4-2-.6-1 0-2.200 1.300-2.200H17a4 4 0 0 0 4-4C21 6.600 17 3 12 3z"/><circle cx="7.500" cy="11" r="1"/><circle cx="10" cy="7.500" r="1"/><circle cx="15" cy="7.500" r="1"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.200-4.200"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  camera: '<path d="M3 8a2 2 0 0 1 2-2h2l1.500-2h7L17 6h2a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.500"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  lock: '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  inbox: '<path d="M3 13l2.500-7h13L21 13v6H3z"/><path d="M3 13h5l1 2h6l1-2h5"/>',
};

export function icon(name, size = 16, cls = "") {
  return `<svg class="ico ${cls}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[name] || ""}</svg>`;
}

export const LOGO = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7S2 12 2 12z" stroke="var(--accent)" stroke-width="1.700" stroke-linejoin="round"/><circle cx="12" cy="12" r="3.200" fill="var(--accent)"/><path d="M12 5v2M12 17v2" stroke="var(--accent)" stroke-width="1.700" stroke-linecap="round"/></svg>`;
