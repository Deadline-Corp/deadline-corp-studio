# DEADLINE — Project Context (consolidated from MemPalace 2026-05-11)

> Web / automation / AI agency. Founders private. **Never surface founder names on the site.**

---

## 1. Repo layout (D:\Projects\Deadline)

```
D:\Projects\Deadline\
├── index.html                              # LIVE site — root, GitHub-tracked, deployed via Pages
├── forge.html                              # Forge v3.01 PoC (centered hero PoC)
├── modes.css / modes.js                    # Magic-toggle: 3 visual modes (Resn / Ultranoir / Active-Theory)
├── README.md                               # Public README
├── .github/workflows/pages.yml             # GitHub Pages CI (currently being tuned, see commits 2026-05-11)
├── .nojekyll
│
├── Site_obsidian_source_of_truth/
│   └── index.html                          # Old "source of truth" copy from Obsidian (May 10) — kept for diff. GitHub root index.html is the LATEST.
│
├── Prototypes/                             # Design experiments (NOT on GitHub — local-only)
│   ├── 01_swiss_editorial.html .. 10_neubrutalist.html   # 10 design directions
│   ├── Mix_v2/ (mix_01..mix_10)            # Mix v2 explorations
│   ├── Mix_v3_Forge/forge_v3_01.html       # Anchor PoC (Forge style)
│   └── index.html                          # Prototypes index
│
├── Research/Reference_sites.md             # Reference sites notes
├── AI_Content_Factory/00_Initial_Plan_Hermes_OpenClaw.md   # Content Factory plan (Hermes + OpenClaw)
└── Hermes_vs_OpenClaw_architecture.md      # Architecture note: Hermes (brain) vs OpenClaw (orchestrator)
```

**Origin of files:**
- Root `index.html` / `forge.html` / `modes.*` / `README.md` / `.github/` — from GitHub `Deadline-Corp/deadline-corp-studio` (cloned 2026-05-11, HEAD = `342684c`)
- `Site_obsidian_source_of_truth/`, `Prototypes/`, `Research/`, `AI_Content_Factory/`, `Hermes_vs_OpenClaw_architecture.md` — copied from `D:\Obsidian\Vault\Vault\Business\Projects\Deadline\` (NOT on GitHub)

---

## 2. GitHub + deploy state (as of 2026-05-11)

| Field | Value |
|------|-------|
| **Org/Repo** | `Deadline-Corp/deadline-corp-studio` (transferred from `NickLuck777/web-dev-studio-site`) |
| **URL** | https://github.com/Deadline-Corp/deadline-corp-studio |
| **Default branch** | `main` |
| **Visibility** | Public |
| **Latest commit** | `342684c` — `ci(pages): revert invalid administration: write key (not allowed in workflow permissions)` |
| **GitHub Pages** | Being enabled via `.github/workflows/pages.yml` (Actions workflow). Recent commits = CI iteration. Live URL TBD once the workflow first runs green. |
| **Old Cloudflared tunnel** | `https://texas-reliable-southern-continued.trycloudflare.com/` — DEAD. Was launched from `/tmp/studio-site-deploy`. Relaunch by `cloudflared tunnel --url http://localhost:PORT` if needed for sharing. |
| **Collaborators** | A1exxx (write access on the personal repo before transfer). Verify org-level roles after migration to org. |

Recent commit timeline (newest first):
```
342684c  ci(pages): revert invalid administration: write key
d350795  ci(pages): add administration:write so configure-pages can createPagesSite
07d4e58  ci(pages): set enablement: true so first run creates the Pages site
d72c6e9  ci: enable GitHub Pages via Actions workflow
d50e4af  Merge: magic mode toggle (3 visual modes with theatrical transitions)
09138d9  magic-toggle: phase 10 — visuals: better backdrops + alignment cleanup
a8e5899  Revert: restore magic-toggle button and themes
2abf9b5  magic-toggle: pause — restore index.html to clean main state
99ebefe  magic-toggle: phase 9 — interactive Resn/Ultranoir/Active-Theory layer
d8e7fe4  magic-toggle: phase 8 — page chrome + radical per-theme grid layouts
```

---

## 3. Brand identity — LOCKED PHRASING (never change without explicit permission)

**Name:** `DEADLINE`
**Founded narrative:** since 2025
**Copyright:** `© 2025-2026 DEADLINE`

**Tagline** (hero-meta, third line):
- RU: `// дедлайны нас боятся`
- EN: `// deadlines fear us`

**Hero slogan:**
- RU: `Меньше слов. Больше результата.`
- EN: `Skip the talk. Deliver the impact.`

**Manifesto:**
- RU: `Мы — DEADLINE. У нас ничего не горит.`
- EN: `We are DEADLINE. Nothing's on fire here.`

**Contact headline:**
- RU: `Пиши — ответим раньше, чем уберёшь руки от клавиатуры.`
- EN: `Write — we'll reply before your hands leave the keyboard.`

**Contact CTA body:** `Берёмся за всё. План и срок прилетят прежде, чем ты допьёшь кофе.`

**Service closings:**
- Web: `Сипим продукт. Не «MVP for now».`
- Automation: `Горячая автоматизация — та, про которую забыл.`
- AI Agents: `Не chatbot. Production-assistant с метриками.`

---

## 4. Hard content rules — NEVER REINTRODUCE

1. **NEVER** show "Двое инженеров" / "two engineers" / team-size hints — makes the team look small/risky.
2. **NEVER** show fixed-duration deadlines like "8-week guarantee" — different work has different deadlines.
3. **NEVER** show defensive disclaimers ("если не справимся", "не возьмёмся", "порекомендуем кого-то") — DEADLINE is mega-confident, takes on everything.
4. **NEVER** show urgency tricks ("1 slot remaining for Q3").
5. **NEVER** surface founder names anywhere on the site — user wants privacy. Footer + manifesto signature show only `DEADLINE · since 2025`.
6. Section eyebrows (`01 / Services` etc) exist in HTML but **HIDDEN** via `.section-eyebrow { display: none; }` for fast revert if needed.
7. **NEVER** use year `2024` anywhere — all timestamps must be 2025 or 2026 (matches "since 2025" narrative).
8. **Founders section was DELETED** — do not reintroduce without explicit request.

---

## 5. Contacts (real, final, wired in 4 places: contact-block primary + secondary CTAs, footer email + telegram)

- **Email:** `corpdeadline@gmail.com`
- **Telegram:** `@deadline_corp` → https://t.me/deadline_corp

---

## 6. Stats (locked)

| # | Number | Label |
|---|--------|-------|
| 1 | `12+`  | Projects shipped to production |
| 2 | `0`    | Missed deadlines since day one |
| 3 | `100%` | Returning clients or referrals |
| 4 | `8`    | Industries served and counting |

---

## 7. Case studies (3 cards on site)

1. **VRP — VIP Rental Phuket · 2026 · Web + AI**
   Next.js + GPT-4 AI-консьерж, +32% conversion, 73% inquiries handled without human, 80+ properties.

2. **KD — KeyDrop · 2025 · Automation + MiniApp**
   Telegram MiniApp e-commerce, Steam codes delivery, 1000+ orders/month, 99.99% uptime, 18 months.
   *(Originally "Backdoor Store" — anonymized to "KeyDrop" by user request.)*

3. **RA — RA Project · 2025-2026 · Data + AI**
   On-chain analytics, 12 blockchain networks, ClickHouse + Kafka, 41M+ blocks, 4 agents in prod.

---

## 8. User voice / tone

Confident, slightly cocky, plays semantically with the DEADLINE name. **No** defensive language, **no** corporate buzzwords, **no** "if/maybe/perhaps". Always `Делаем / Берёмся / Закрываем / Сипим`. Russian primary, selective English for tech terms (`web`, `automation`, `MiniApp`, `RAG`, etc — ok). Manifesto and contact headlines must PUNCH.

---

## 9. Tech setup

**Bilingual RU/EN — critical CSS (do NOT use `display: revert`, breaks ticker layout):**
```css
body:not(.lang-en) :where(.lang-en) { display: none !important; }
body.lang-en :where(.lang-ru) { display: none !important; }
```

- 95 `lang-ru` / 95 `lang-en` spans balanced.
- Lang toggle in nav with copper-active underline.
- Default RU. EN persists via URL hash `#en` + `localStorage 'deadline-lang'` + `navigator.language` autodetect.

**Visual modes (magic-toggle, see `modes.css` / `modes.js`):**
- Three interactive modes: **Resn**, **Ultranoir**, **Active-Theory** (phase-9 work, merged in `d50e4af`).
- Theatrical transitions between modes.
- Per-theme grid layouts (phase 8).

**Workflow (CONFIRMED preference):**
- **Batch commits.** Do NOT commit every small edit. Accumulate changes locally, then one large commit with comprehensive message.
- Now that the project lives at `D:\Projects\Deadline` (not Obsidian), edit directly here and commit/push from this directory.

**Reference style anchor:** `Prototypes/Mix_v3_Forge/forge_v3_01.html` (the user's hero PoC — keep as visual reference for Forge style).

---

## 10. Open tasks (from MemPalace, 2026-05-11)

- [ ] Land GitHub Pages workflow green (currently iterating in `ci(pages):` commits — `342684c` reverted `administration:write`, so the Pages site likely still needs to be created via UI or via a workflow with the correct permissions).
- [ ] Decide GitHub org migration finalization details — repo is already at `Deadline-Corp/deadline-corp-studio`. Verify admin/maintain roles for A1exxx.
- [ ] Favicon + OG-image 1200x630 for Telegram / Slack link previews (design-skill can generate).
- [ ] Real testimonials replacement when VRP / KeyDrop / RA clients provide quotes (current testimonials are placeholders in the marquee).
- [ ] Future scope: pricing page, blog, case-study deep-dives.
- [ ] AI Content Factory PoC: see `AI_Content_Factory/00_Initial_Plan_Hermes_OpenClaw.md` — VPS selection, API keys, vector DB choice (Milvus vs Pinecone), keyword list.

---

## 11. Hooks & quirks

- `PostToolUse "preview panel visible"` hooks are informational only.
- Read-before-Edit reminders are preventive, NOT blocking — Edits succeed if file was Read earlier in same session.
- Git Bash on Windows rewrites `gh api /repos/...` to a filesystem path — use `gh api repos/...` (no leading slash).
- MemPalace `add_drawer` fails "Internal tool error" if content > ~3KB or if called in parallel — keep drawers small and serial.
- Vault Obsidian Git plugin auto-commits every 5 min — **never run git commands inside the Obsidian vault**. This project now lives outside the vault, so normal git is fine here.

---

## 12. GitHub auth

Recommended: `gh auth login --hostname github.com --git-protocol https --web` once, then `git push` works through the GitHub CLI credential helper.

For automation: load a personal access token from a local env file (NOT committed). Reference it via `$GITHUB_TOKEN` only. The repo URL must stay clean (no embedded credentials).

---

## 13. Where the *full* canonical knowledge lives

This file is a working snapshot. The verbatim source-of-truth knowledge lives in MemPalace (`wing=vault`, `room=decisions_design`, drawers filed 2026-05-11). To re-pull: `mempalace_search query="Deadline project"` or `mempalace_search query="deadline site pending tasks"`.

Related MemPalace tunnels: `vault/decisions_design` ↔ project repo. Knowledge graph entities: `DEADLINE` (project), `Deadline-Corp` (GitHub org).

---

## 14. Daily diary (mandatory for any Deadline session)

**Rule set 2026-05-19 by user.** Any Claude session working in this repo must mirror progress into a daily diary file. Canonical location is Obsidian Vault.

**Where**
- Folder: `D:/Obsidian/Vault/Vault/Business/Projects/Deadline/Daily_Diary/`
- One file per date: `YYYY-MM-DD.md` (e.g. `2026-05-19.md`)
- File H1: `# YYYY-MM-DD — День_недели` (RU day-of-week so it scans naturally for the user)
- Vault is auto-committed and auto-pushed by Obsidian Git plugin every 5 min. **DO NOT** run any `git` commands inside `D:/Obsidian/Vault/` — just `Write`/`Edit` files.

**When**
- After every **logical step** — append an entry. Logical step = a unit of work you would describe out loud as "we shipped X" (feature deployed, migration applied, prompt fix verified). NOT every tool call / every read.
- At **end of session** — append a one-paragraph EOD summary at the bottom.

**Entry format (per step)**
```
## HH:MM — Title in imperative
- short bullet on what was done
- what was verified (curl output, test result)
- link to commit SHA or Railway deployment id
```

**EOD summary format**
One paragraph (Russian, since user reads in RU), 2-4 sentences, plain text (no headings, no bullets). What is concretely DONE today + what is the next step / blocker. Scan-readable for the user opening it in the morning.

**History**
Originally tried Notion (page `35cc12e0...`, sub-page Belikov) on 2026-05-19. Failed: MCP server is logged in to Eon Stacks Wiki workspace (where RA-Project lives), while user's "Дневник" page is in his personal workspace. Either reconfigure MCP with a personal-workspace token or keep Obsidian as canonical. We chose Obsidian — synced automatically, no MCP fragility, version-controlled by Obsidian Git.

**Rule lives in**: this file + MemPalace drawer `deadline/rules/e44aea33fbc8a8327579ed70` (note: the drawer still references Notion — superseded by this section).
