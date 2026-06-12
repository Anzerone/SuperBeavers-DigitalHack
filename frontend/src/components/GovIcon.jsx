const ICONS = {
  app: (
    <>
      <path d="M4 19V5" />
      <path d="M4 19h16" />
      <path d="M8 16V9" />
      <path d="M12 16V7" />
      <path d="M16 16v-5" />
    </>
  ),
  bar: (
    <>
      <path d="M4 6h16" />
      <path d="M4 12h12" />
      <path d="M4 18h8" />
    </>
  ),
  grid: (
    <>
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </>
  ),
  list: (
    <>
      <path d="M8 6h12" />
      <path d="M8 12h12" />
      <path d="M8 18h12" />
      <path d="M4 6h.01" />
      <path d="M4 12h.01" />
      <path d="M4 18h.01" />
    </>
  ),
  file: (
    <>
      <path d="M7 3h7l5 5v13H7z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6" />
      <path d="M9 17h6" />
    </>
  ),
  upload: (
    <>
      <path d="M12 16V4" />
      <path d="m8 8 4-4 4 4" />
      <path d="M5 16v4h14v-4" />
    </>
  ),
  attachment: (
    <path d="M8 12.5 13.8 6.7a3 3 0 0 1 4.2 4.2l-7 7a5 5 0 0 1-7.1-7.1l7.8-7.8" />
  ),
  bolt: (
    <path d="M13 2 4.5 13.5H11L10 22l8.5-11.5H12L13 2z" />
  ),
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2" />
      <path d="M12 20v2" />
      <path d="m4.9 4.9 1.4 1.4" />
      <path d="m17.7 17.7 1.4 1.4" />
      <path d="M2 12h2" />
      <path d="M20 12h2" />
      <path d="m4.9 19.1 1.4-1.4" />
      <path d="m17.7 6.3 1.4-1.4" />
    </>
  ),
  moon: (
    <path d="M20 13.5A8.5 8.5 0 0 1 10.5 4 7 7 0 1 0 20 13.5z" />
  ),
  stop: (
    <>
      <circle cx="12" cy="12" r="9" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v12" />
      <path d="m8 12 4 4 4-4" />
      <path d="M5 20h14" />
    </>
  ),
  report: (
    <>
      <path d="M5 20V4h14v16z" />
      <path d="M8 15v-4" />
      <path d="M12 15V8" />
      <path d="M16 15v-2" />
    </>
  ),
  warning: (
    <>
      <path d="M12 4 3 20h18z" />
      <path d="M12 9v5" />
      <path d="M12 17h.01" />
    </>
  ),
  priority: (
    <>
      <path d="M12 3c3 3 5 5.4 5 9a5 5 0 0 1-10 0c0-2.2 1.2-4 3-6" />
      <path d="M12 15v-4" />
    </>
  ),
  building: (
    <>
      <path d="M4 20h16" />
      <path d="M6 20V8l6-4 6 4v12" />
      <path d="M9 10h.01" />
      <path d="M12 10h.01" />
      <path d="M15 10h.01" />
      <path d="M9 14h.01" />
      <path d="M12 14h.01" />
      <path d="M15 14h.01" />
    </>
  ),
  folder: (
    <>
      <path d="M3 7h7l2 2h9v11H3z" />
      <path d="M3 7V5h7l2 2" />
    </>
  ),
  cluster: (
    <>
      <circle cx="7" cy="7" r="3" />
      <circle cx="17" cy="7" r="3" />
      <circle cx="12" cy="17" r="3" />
      <path d="M9.5 8.5 11 14" />
      <path d="M14.5 8.5 13 14" />
    </>
  ),
  mood: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M8.5 10h.01" />
      <path d="M15.5 10h.01" />
      <path d="M8.5 15c2 1.5 5 1.5 7 0" />
    </>
  ),
  bell: (
    <>
      <path d="M6 10a6 6 0 0 1 12 0c0 5 2 5 2 7H4c0-2 2-2 2-7" />
      <path d="M10 20a2 2 0 0 0 4 0" />
    </>
  ),
  trend: (
    <>
      <path d="M4 17 9 12l4 4 7-9" />
      <path d="M15 7h5v5" />
    </>
  ),
  new: (
    <>
      <path d="M5 5h14v14H5z" />
      <path d="M12 8v8" />
      <path d="M8 12h8" />
    </>
  ),
  check: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8 12 3 3 6-7" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  spinner: (
    <>
      <path d="M12 3a9 9 0 1 0 9 9" />
      <path d="M21 3v6h-6" />
    </>
  ),
  chevronDown: <path d="m7 10 5 5 5-5" />,
  chevronUp: <path d="m7 14 5-5 5 5" />,
  close: (
    <>
      <path d="M6 6 18 18" />
      <path d="M18 6 6 18" />
    </>
  ),
  arrowRight: (
    <>
      <path d="M4 12h16" />
      <path d="m14 6 6 6-6 6" />
    </>
  ),
  arrowLeft: (
    <>
      <path d="M20 12H4" />
      <path d="m10 6-6 6 6 6" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M16 16 21 21" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16" />
      <path d="M9 7V4h6v3" />
      <path d="M7 7l1 14h8l1-14" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </>
  ),
  star: (
    <path d="m12 4 2.4 4.9 5.4.8-3.9 3.8.9 5.4-4.8-2.5-4.8 2.5.9-5.4-3.9-3.8 5.4-.8z" />
  ),
  plus: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  save: (
    <>
      <path d="M5 4h12l2 2v16H5z" />
      <path d="M8 4v6h8V4" />
      <path d="M8 18h8" />
    </>
  ),
  feedback: (
    <>
      <path d="M4 5h16v11H8l-4 4z" />
      <path d="M8 9h8" />
      <path d="M8 13h5" />
    </>
  ),
  location: (
    <>
      <path d="M12 21s7-5.5 7-12a7 7 0 0 0-14 0c0 6.5 7 12 7 12z" />
      <circle cx="12" cy="9" r="2.5" />
    </>
  ),
  home: (
    <>
      <path d="m4 11 8-7 8 7" />
      <path d="M6 10v10h12V10" />
      <path d="M10 20v-6h4v6" />
    </>
  ),
  road: (
    <>
      <path d="M8 21 11 3" />
      <path d="m13 3 3 18" />
      <path d="M12 7v2" />
      <path d="M12 13v2" />
      <path d="M12 19v1" />
    </>
  ),
  education: (
    <>
      <path d="m3 8 9-4 9 4-9 4z" />
      <path d="M7 10v5c3 2 7 2 10 0v-5" />
    </>
  ),
  sport: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M5 10c4 0 7-2 9-6" />
      <path d="M9 20c1-5 5-8 11-8" />
    </>
  ),
  medical: (
    <>
      <path d="M10 4h4v6h6v4h-6v6h-4v-6H4v-4h6z" />
    </>
  ),
  tree: (
    <>
      <path d="M12 21v-7" />
      <path d="M8 14h8l-2-4h3L12 3 7 10h3z" />
    </>
  ),
  bus: (
    <>
      <rect x="5" y="4" width="14" height="13" rx="2" />
      <path d="M7 9h10" />
      <path d="M8 20h.01" />
      <path d="M16 20h.01" />
    </>
  ),
  social: (
    <>
      <circle cx="9" cy="8" r="3" />
      <circle cx="17" cy="10" r="2.5" />
      <path d="M4 20a5 5 0 0 1 10 0" />
      <path d="M14 20a4 4 0 0 1 6 0" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 5 6v6c0 4 3 7 7 9 4-2 7-5 7-9V6z" />
      <path d="m9 12 2 2 4-5" />
    </>
  ),
  energy: (
    <path d="M13 3 5 14h7l-1 7 8-12h-7z" />
  ),
  eco: (
    <>
      <path d="M5 19c8 0 13-5 14-14-9 1-14 6-14 14z" />
      <path d="M5 19c3-5 7-8 12-10" />
    </>
  ),
  waste: (
    <>
      <path d="M5 7h14" />
      <path d="M9 7V4h6v3" />
      <path d="M7 7l1 14h8l1-14" />
    </>
  ),
  signal: (
    <>
      <path d="M4 14a8 8 0 0 1 16 0" />
      <path d="M8 14a4 4 0 0 1 8 0" />
      <path d="M12 18h.01" />
    </>
  ),
  construction: (
    <>
      <path d="M4 20h16" />
      <path d="M7 20V8l5-4 5 4v12" />
      <path d="M7 12h10" />
    </>
  ),
  culture: (
    <>
      <path d="M5 5c3 0 5 1 7 3 2-2 4-3 7-3v8c-3 0-5 1-7 3-2-2-4-3-7-3z" />
      <path d="M12 8v8" />
    </>
  ),
  map: (
    <>
      <path d="M4 6 9 4l6 2 5-2v14l-5 2-6-2-5 2z" />
      <path d="M9 4v14" />
      <path d="M15 6v14" />
    </>
  ),
  shop: (
    <>
      <path d="M5 9h14l-1 11H6z" />
      <path d="M8 9a4 4 0 0 1 8 0" />
    </>
  ),
  work: (
    <>
      <rect x="4" y="7" width="16" height="13" rx="2" />
      <path d="M9 7V5h6v2" />
      <path d="M4 12h16" />
    </>
  ),
  passport: (
    <>
      <rect x="6" y="3" width="12" height="18" rx="2" />
      <circle cx="12" cy="10" r="3" />
      <path d="M9 16h6" />
    </>
  ),
  travel: (
    <>
      <path d="M4 17 20 8" />
      <path d="m8 15-2 5 5-3" />
      <path d="m14 11 5 5 1-3-4-5" />
    </>
  ),
  youth: (
    <>
      <path d="M7 21V9l5-5 5 5v12" />
      <path d="M9 21v-7h6v7" />
      <path d="M10 9h4" />
    </>
  ),
  property: (
    <>
      <path d="M4 20h16" />
      <path d="M6 20V8l6-4 6 4v12" />
      <path d="M10 20v-6h4v6" />
    </>
  ),
  registry: (
    <>
      <circle cx="9" cy="12" r="4" />
      <circle cx="15" cy="12" r="4" />
      <path d="M12 8v8" />
    </>
  ),
  veterinary: (
    <>
      <circle cx="6" cy="9" r="2" />
      <circle cx="10" cy="6" r="2" />
      <circle cx="14" cy="6" r="2" />
      <circle cx="18" cy="9" r="2" />
      <path d="M7 18c1-4 9-4 10 0 1.2 4-3 4-5 2-2 2-6.2 2-5-2z" />
    </>
  ),
  document: (
    <>
      <path d="M7 3h7l5 5v13H7z" />
      <path d="M14 3v5h5" />
      <path d="M9 13h6" />
    </>
  ),
}

export default function GovIcon({ name = 'document', className = 'h-5 w-5', title, ...props }) {
  const icon = ICONS[name] || ICONS.document

  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden={title ? undefined : 'true'}
      role={title ? 'img' : undefined}
      {...props}
    >
      {title && <title>{title}</title>}
      {icon}
    </svg>
  )
}
