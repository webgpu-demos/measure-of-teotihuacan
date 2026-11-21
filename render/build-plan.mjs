// Write PLAN.md from docs/story.js, so the written plan, the film and the app
// always carry the same steps and wording.  Usage: node render/build-plan.mjs
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TITLE, SUBTITLE, PLAN_SUMMARY, CHAPTERS, TIMELINE, HYPOTHESES, SKEPTICS, ROLES, OUTCOMES, WEEKS, FIRST_CALL_QUESTIONS, SOURCES,
} from '../docs/story.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
const out = [];
const p = (...lines) => out.push(...lines);

p(`# ${TITLE}`, '', SUBTITLE, '');
p(`Generated from \`docs/story.js\`, the same storyboard that drives the film (\`docs/film.mp4\`, ${fmt(TIMELINE.duration)}) and the walkthrough app (\`docs/index.html\`). Edit the storyboard, not this file.`, '');

p('## The plan in one paragraph', '');
p(PLAN_SUMMARY, '');

p('## Steps', '');
CHAPTERS.forEach((ch, i) => {
  const tc = TIMELINE.chapters[i];
  p(`### ${i + 1}. ${ch.kicker}: ${ch.title}`, '');
  p(`*Film ${fmt(tc.start)}–${fmt(tc.end)}*`, '');
  p(...ch.lines.map((l) => `> ${l}`), '');
  p(ch.app.summary, '');
  p(...ch.app.points.map((x) => `- ${x}`), '');
  if (ch.app.who) p(`Who: ${ch.app.who.join(', ')}`, '');
});

p('## Hypotheses (fixed before the test)', '');
p('| Hypothesis | Note | Detail |', '|---|---|---|', ...HYPOTHESES.map((h) => `| ${h.label} | ${h.note} | ${h.detail} |`), '');
p('## Skeptic checks', '');
p('| Objection | Check that runs | What would change our mind |', '|---|---|---|', ...SKEPTICS.map(([a, b, c]) => `| ${a} | ${b} | ${c} |`), '');
p('## Roles', '');
p(...ROLES.map(([r, b, t]) => `- **${r}** (${b.toLowerCase()}): ${t}`), '');
p('## Outcomes', '');
p(...OUTCOMES.map(([, b, t]) => `- **${b}.** ${t}`), '');
p('## Timeline', '');
p(...WEEKS.map(([w, t]) => `- **${w}:** ${t}`), '');
p('## Questions for the first call', '');
p(...FIRST_CALL_QUESTIONS.map((q) => `- [ ] ${q}`), '');
p('## Sources', '');
p(...SOURCES.map((s) => `- [${s.title}](${s.url}), ${s.where}`), '');

fs.writeFileSync(path.join(ROOT, 'PLAN.md'), out.join('\n'));
console.log('wrote PLAN.md', out.length, 'lines');
