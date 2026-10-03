> Исторический отчёт о прежней реализации. Актуальный результат: [переработка 2026-09-15](2026-09-15-synonym-rewrite.md).

# Synonym Pilot Validation Report

Date: 2026-09-14

Scope: independent validation for `docs/plans/2026-09-14-001-feat-fair-synonym-practice-plan.md`. This report is read-only analysis plus proposed teaching material. It does not change application code, tests, cards, progress, plan, or README.

## Executive Recommendations

1. Ship the fairness contract before broad material work: `free_expression` must never hide a required lemma, `target_usage` must show the target word and stay practice-only, and accepted alternatives or defective prompts must be SRS-neutral for the hidden target.
2. Keep the pilot small. The local CSV/live data supports about 8 strong pilot contrasts plus a few historical confusion candidates; it does not justify padding to 20 pairs.
3. Start with `verfügbar` / `zugänglich` as verified sample material, but do not label it a proven historical user error in the live database. The live cards contain `zugänglich` and `vorhanden`, not `verfügbar`.
4. Treat duplicate cards, preposition variants, and true synonyms differently. For example `sich freuen auf` / `sich freuen über` is a grammar contrast, while `benutzen` / `verwenden` is a partial synonym contrast.
5. Button and contrast tasks need all valid choices in the task snapshot. Some contexts have two valid answers; those should be accepted and explained, not forced into a single index.

## Data Audited

Local CSV files:

- `data/deutsch A2 words Swetlana.csv`: 54 rows.
- `data/verbs v2.csv`: 85 rows.
- `data/verbs mit prapositionen.csv`: 40 rows.

Legacy SQLite:

- `progress.sqlite3`: 178 words, 1 unanswered task, 0 reviews, 0 progress rows. It is useful as a lexical snapshot only, not as historical error evidence.

Live PostgreSQL, read-only:

- 566 words, 1,432 tasks, 1,379 reviews, 366 answer evaluations, 8 card corrections.
- Review aggregate: `flashcard_ru_de` has 201 rejected reviews and 88 partial reviews; `cloze` has 9 rejected reviews; `choice_ru_de` has 3 rejected reviews.
- Answer evaluation aggregate: 223 `rejected`, 89 `partial`, 50 `accepted`, 4 failed evaluations.
- No secrets were copied. Free-form learner answers were not reproduced; wrong-answer history was summarized only when an answer exactly matched another known card lemma/headword.

## Historical Evidence vs Verified Samples

Historical user-error evidence from live reviews:

- Repeated card-correction signals are concentrated around `sich gewöhnen an Akk`, not around the synonym plan's first pair.
- Exact known-card wrong answers include `wischen` answered as `den Boden sauber machen`, `entfernen` answered as `entsorgen`, `entsorgen` answered as `zerstören`, and `sich bewegen` answered as `bewegen`. These are actual historical confusion signals, but they are not all synonym problems.
- The live `zugänglich` card has two correct reviews and no observed wrong close-word answer in the reviewed sample.
- `verfügbar` was not found as a live card or local CSV row. It is therefore a verified teaching sample, not a local-history finding.

Verified sample material:

- `verfügbar` / `zugänglich`.
- `zugänglich` / `vorhanden` because live cards contain both broad Russian cues around "available/accessible/existing".
- CSV/live duplicate-cue pairs: `benutzen` / `verwenden`, `beginnen` / `anfangen`, `sich erholen` / `ausruhen`, `vorschlagen` / `anbieten`, `sich freuen auf` / `sich freuen über`, `bekommen` / `erhalten`, `schrecklich` / `furchtbar`.

Unverified or low-priority candidates:

- `herunterladen` / `hinunterladen`: local duplicate cue, but Duden validates `herunterladen`; `hinunterladen` did not resolve as a comparable Duden entry during this pass. Treat as import cleanup or regional/style review, not a first pilot contrast.
- Duplicate exact cards such as `der Vorsatz`, `die Gewohnheit`, `die Vergangenheit`, `die Ausnahme`, and repeated reflexive verbs should be deduplicated or merged before being framed as semantic contrasts.

## Sources Used

- Duden `verfügbar`: https://www.duden.de/rechtschreibung/verfuegbar
- Duden `zugänglich`: https://www.duden.de/rechtschreibung/zugaenglich
- Duden `geöffnet`: https://www.duden.de/rechtschreibung/geoeffnet
- Duden `vorhanden`: https://www.duden.de/rechtschreibung/vorhanden
- Duden `benutzen`: https://www.duden.de/rechtschreibung/benutzen
- Duden `verwenden`: https://www.duden.de/rechtschreibung/verwenden
- Duden `beginnen`: https://www.duden.de/rechtschreibung/beginnen
- Duden `anfangen`: https://www.duden.de/rechtschreibung/anfangen
- Duden `erholen`: https://www.duden.de/rechtschreibung/erholen
- Duden `ausruhen`: https://www.duden.de/rechtschreibung/ausruhen
- Duden `vorschlagen`: https://www.duden.de/rechtschreibung/vorschlagen
- Duden `anbieten`: https://www.duden.de/rechtschreibung/anbieten
- Duden `freuen`: https://www.duden.de/rechtschreibung/freuen
- Duden `bekommen`: https://www.duden.de/rechtschreibung/bekommen
- Duden `erhalten`: https://www.duden.de/rechtschreibung/erhalten
- Duden `schrecklich`: https://www.duden.de/rechtschreibung/schrecklich
- Duden `furchtbar`: https://www.duden.de/rechtschreibung/furchtbar
- Duden `herunterladen`: https://www.duden.de/rechtschreibung/herunterladen
- Duden `leiden`: https://www.duden.de/rechtschreibung/leiden
- Duden `entsorgen`: https://www.duden.de/rechtschreibung/entsorgen
- Duden `entfernen`: https://www.duden.de/rechtschreibung/entfernen

These sources substantiate the listed senses and useful register/style notes. They do not prove that every invented or source-data example below has a unique answer; each proposed prompt must still be reviewed as original teaching material with its learner-visible situation cues.

## Verified Teaching Materials

The examples and contrast questions in this section are reviewed proposed original material, based on the audited local CSV/live lexical data plus dictionary sense checks. They are not copied dictionary examples unless a source is explicitly named, and the source checks do not by themselves prove single-answer uniqueness.

### 1. `verfügbar` / `zugänglich`

Source sense:

- `verfügbar`: currently at someone's disposal or present for immediate use.
- `zugänglich`: reachable/enterable, usable by someone, or open to people/ideas.
- Overlap: information, databases, documents, products in online systems, or resources can be both `verfügbar` and `zugänglich` when the intended sense is "available for use/access". Distinction: `verfügbar` emphasizes availability; `zugänglich` emphasizes access.

`verfügbar`

- Simple German definition: Man kann es jetzt benutzen oder bekommen.
- Russian sense cue: есть в наличии / можно получить или использовать сейчас.
- Examples:
  - Das Produkt ist wieder verfügbar.
  - Im Seminar sind noch drei Plätze verfügbar.
- Progressive hint: Denk an Vorrat, Zeit, Plätze oder Mittel: Es ist da und kann genutzt werden.

`zugänglich`

- Simple German definition: Man kann zu einem Ort hineingehen oder Informationen benutzen.
- Russian sense cue: есть доступ / можно попасть внутрь или получить доступ.
- Examples:
  - Das Gelände ist für Besucher zugänglich.
  - Die Datenbank ist für alle Mitarbeitenden zugänglich.
- Progressive hint: Denk an Zugang: Man kommt hinein oder darf etwas nutzen.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Im Geschäft ist das Produkt wieder ____. | `verfügbar`, `erhältlich`, `auf Lager` if free text permits phrases | Visible stock cue makes availability the intended sense. The bare sentence "Das Produkt ist wieder ____" is underdetermined because an online-access situation can also invite `zugänglich`. |
| Im Online-Portal ist das Produkt wieder ____. | `verfügbar`, `zugänglich` | Both can work if the product/page/resource can be used or accessed online; feedback should name availability vs access focus. |
| Das Gelände ist für Besucher ____. | `zugänglich`, also `geöffnet`/`offen` if offered | Access for visitors; `verfügbar` would suggest the site is available for use/booking, so do not use this as a hidden-target recall unless alternatives are accepted. |
| Die Datenbank ist für alle Mitarbeitenden ____. | `zugänglich`, `verfügbar` | Both can work: access vs availability. Accept both and explain the focus. |
| Sind für morgen noch Termine ____? | `verfügbar`, `frei` if offered | Available appointments; the time/booking cue must be visible. |

### 2. `zugänglich` / `vorhanden`

Source sense:

- `vorhanden`: exists / is present.
- `zugänglich`: access is possible.
- Overlap is limited. A thing can exist but not be accessible.

`vorhanden`

- Simple German definition: Es gibt das; es ist da.
- Russian sense cue: имеющийся / существующий.
- Examples:
  - Die nötigen Unterlagen sind vorhanden.
  - Es sind noch Vorräte vorhanden.
- Progressive hint: Frage: Gibt es das überhaupt?

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Für die Anmeldung brauchen wir drei Dokumente; alle Unterlagen sind ____. | `vorhanden` | The prompt visibly asks whether the required documents exist/are present. The bare sentence "Die Unterlagen sind ____" is underdetermined and can also support `zugänglich` if access is meant. |
| Die Unterlagen sind für alle Kursteilnehmer ____. | `zugänglich`, `verfügbar` if material is made available | Access or availability to people. |
| Im Raum sind genug Stühle ____. | `vorhanden` | Existence/presence. |

### 3. `benutzen` / `verwenden`

Source sense:

- `benutzen`: use something according to its purpose; can include using a route, entrance, taxi, toilet, tool, word.
- `verwenden`: use/apply something for a purpose, method, ingredient, device, time/money/effort; can overlap with `benutzen`.
- Overlap: tools, devices, words, methods.

`benutzen`

- Simple German definition: Man gebraucht etwas.
- Russian sense cue: пользоваться / использовать предмет или средство.
- Examples:
  - Er benutzt seinen Laptop täglich.
  - Bitte benutzen Sie den vorderen Eingang.
- Progressive hint: Denk an ein Ding oder einen Weg, den man gebraucht.

`verwenden`

- Simple German definition: Man nimmt etwas für einen bestimmten Zweck.
- Russian sense cue: применять / использовать для цели.
- Examples:
  - Ich verwende Olivenöl für die Soße.
  - Im Unterricht verwenden wir dieses Lehrbuch.
- Progressive hint: Denk an Zweck, Methode, Zutat oder Material.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Er ____ seinen Laptop täglich. | `benutzt`, `verwendet` | Both are natural; `benutzt` is more everyday. |
| Für die Soße ____ ich Olivenöl. | `verwende`, `benutze` | Both possible; `verwende` fits ingredient/purpose best. |
| Bitte ____ Sie den vorderen Eingang. | `benutzen`; `verwenden` is much less idiomatic but should not be hard-rejected in broad semantic free text | Public-sign usage strongly prefers `benutzen`; use this as a register/idiom teaching point or a "best fit" choice, not a strict single-answer recall. |

### 4. `beginnen` / `anfangen`

Source sense:

- Both mean to begin/start.
- `beginnen` is often neutral or somewhat more formal.
- `anfangen` is common in everyday speech and has extra uses such as "what to do with something" or starting a topic.

`beginnen`

- Simple German definition: Etwas startet oder man startet etwas.
- Russian sense cue: начинать / начинаться, нейтрально или официальнее.
- Examples:
  - Der Unterricht beginnt um neun Uhr.
  - Wir beginnen mit der Arbeit.
- Progressive hint: Passt gut zu Unterricht, Vorstellung, Arbeit, Prozess.

`anfangen`

- Simple German definition: Man startet etwas; etwas fängt an.
- Russian sense cue: начинать, чаще разговорно.
- Examples:
  - Wir fangen gleich an.
  - Was soll ich damit anfangen?
- Progressive hint: Alltagssprache; auch in der Frage "что с этим делать?"

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Complete-sentence choice: "Der Unterricht beginnt um neun Uhr." / "Der Unterricht fängt um neun Uhr an." | both sentences | Both are correct; `beginnen` sounds more neutral/formal. Do not use one identical one-slot cloze here unless the accepted set is form-compatible. |
| Exact cloze: Der Unterricht ____ um neun Uhr. | `beginnt` | `fängt ... an` cannot fill this one-slot shape naturally because the separable prefix belongs near the end. |
| Exact cloze: Der Unterricht ____ um neun Uhr ____. | `fängt ... an` | `beginnt` cannot fill both slots; keep this as an `anfangen` form task. |
| Was soll ich damit ____? | `anfangen` | Idiom "etwas mit etwas anfangen". |

### 5. `sich erholen` / `ausruhen`

Source sense:

- `sich erholen`: regain strength, recover, get over something.
- `ausruhen`: rest in order to gather strength; also rest body parts.
- Overlap: after work/vacation, both can be natural with different focus. Distinction: `erholen` can be recovery from illness/shock; `ausruhen` is the act of resting.

`sich erholen`

- Simple German definition: Man bekommt wieder Kraft.
- Russian sense cue: восстанавливаться / отдыхать и набраться сил.
- Examples:
  - Ich erhole mich am Wochenende.
  - Sie erholt sich von der Krankheit.
- Progressive hint: Ergebnis: danach hat man wieder Kraft.

`ausruhen`

- Simple German definition: Man macht eine Pause und ruht.
- Russian sense cue: отдохнуть / сделать паузу.
- Examples:
  - Ich ruhe mich nach der Arbeit aus.
  - Du solltest dich ein wenig ausruhen.
- Progressive hint: Denk an Pause, Bank, Sofa, kurz nicht arbeiten.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Nach der Arbeit ____ er sich kurz ____. | `ruht ... aus` | A concrete short rest, and the two-slot shape fits the separable verb. Do not list `erholt` as valid for this exact shape. |
| Sie ____ sich langsam von der Krankheit. | `erholt` | Recovery from illness. |
| Complete-sentence choice: "Am Wochenende erholen wir uns." / "Am Wochenende ruhen wir uns aus." | both sentences | Both can work: recovery vs resting. A one-slot cloze "Am Wochenende ____ wir uns" only fits `erholen`; `ruhen ... aus` needs its separable prefix. |

### 6. `vorschlagen` / `anbieten`

Source sense:

- `vorschlagen`: put forward an idea/plan/person for consideration.
- `anbieten`: offer something to someone, including help/service/goods.
- Overlap is contextual: a solution can be proposed or offered, but the grammar and role differ.

`vorschlagen`

- Simple German definition: Man sagt eine Idee, die andere wählen können.
- Russian sense cue: предлагать идею / вариант.
- Examples:
  - Ich schlage einen Spaziergang vor.
  - Sie schlägt eine Lösung vor.
- Progressive hint: Denk an Idee, Plan, Termin, Lösung.

`anbieten`

- Simple German definition: Man gibt jemandem die Möglichkeit, etwas zu bekommen oder zu nutzen.
- Russian sense cue: предлагать помощь / услугу / товар.
- Examples:
  - Ich biete dir meine Hilfe an.
  - Das Hotel bietet Frühstück an.
- Progressive hint: Denk an Angebot: Hilfe, Service, Essen, Produkt.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| In der Gruppe suchen wir eine Idee: Ich ____ einen Spaziergang ____. | `schlage ... vor` | The visible cue makes this an idea/plan. Without that cue, `biete ... an` is possible in service contexts, e.g. a guide offering a walk. |
| Ich ____ dir meine Hilfe ____. | `biete ... an` | Offer of help. |
| Die Firma ____ ihren Kunden einen neuen Service ____. | `bietet ... an` | Service offer to customers. |
| Sie ____ eine Lösung ____. | `schlägt ... vor`, `bietet ... an` when the context is an offered solution | Proposal vs offer; add a learner-visible cue before rejecting either. |

### 7. `sich freuen auf` / `sich freuen über`

Source sense:

- Duden examples confirm `sich freuen auf` for joyful expectation and `sich freuen über` for joy about success/result/event.
- This is a preposition/time contrast, not a synonym pair.

`sich freuen auf`

- Simple German definition: Man ist froh, weil etwas bald kommt.
- Russian sense cue: радоваться предстоящему / ждать с радостью.
- Examples:
  - Ich freue mich auf die Ferien.
  - Wir freuen uns auf deinen Besuch.
- Progressive hint: Zukunft: etwas kommt noch.

`sich freuen über`

- Simple German definition: Man ist froh wegen etwas, das passiert ist oder da ist.
- Russian sense cue: радоваться поводу / тому, что уже есть или случилось.
- Examples:
  - Sie freut sich über das Geschenk.
  - Ich freue mich über deinen Erfolg.
- Progressive hint: Grund ist schon da: Geschenk, Erfolg, Nachricht.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Nächsten Monat beginnen die Ferien. Ich freue mich ____ die Ferien. | `auf` | Future expectation is visible. The bare sentence can also use `über` if the holidays have just been granted or have begun. |
| Die Ferien haben gerade begonnen. Ich freue mich ____ die Ferien. | `über`, sometimes `auf` if another future holiday period is meant | Current/just-realized joy must be visible before treating `auf` as wrong. |
| Das Geschenk liegt schon auf dem Tisch. Sie freut sich ____ das Geschenk. | `über` | Present/received thing. |
| Morgen bekommt sie ein Geschenk. Sie freut sich ____ das Geschenk. | `auf` | Anticipated gift; the original bare sentence must not reject this. |
| Wir freuen uns ____ deinen Besuch morgen. | `auf` | Future visit. |
| Wir freuen uns ____ deinen Besuch gestern. | avoid as a prompt; rephrase to "Wir haben uns über deinen Besuch gestern gefreut." | The default prompt should avoid this awkward edge case. |

### 8. `bekommen` / `erhalten`

Source sense:

- `bekommen`: broad "get/receive", including gifts, pay, mail, permission, illness/state, opportunity, passive-like constructions.
- `erhalten`: receive/obtain, often more formal; also preserve/maintain in other senses.
- Overlap: messages, packages, gifts, permission, awards.

`bekommen`

- Simple German definition: Jemand gibt es mir oder ich kriege es.
- Russian sense cue: получать, разговорнее и шире.
- Examples:
  - Ich bekomme ein Geschenk.
  - Sie bekommt eine Nachricht.
- Progressive hint: Alltag: kriegen/получить; auch Hunger, Angst, Besuch.

`erhalten`

- Simple German definition: Man bekommt etwas, oft offiziell oder schriftlich.
- Russian sense cue: получать, официальнее / письменнее.
- Examples:
  - Sie erhalten eine Nachricht von uns.
  - Er erhielt einen Preis.
- Progressive hint: Brief, Nachricht, Preis, Genehmigung; klingt formeller.

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Ich ____ ein Geschenk. | `bekomme`, `erhalte` | Both work; `bekomme` is everyday, `erhalte` is formal. |
| Sie ____ eine SMS. | `bekommt`, `erhält` | Both work; `bekommt` is more everyday. |
| Ich ____ Hunger. | `bekomme` | State/change; `erhalte Hunger` is wrong. |
| Sie ____ eine Aufenthaltsgenehmigung. | `bekommt`, `erhält` | Both work; `erhält` formal/official. |

### 9. `schrecklich` / `furchtbar`

Source sense:

- Both can mean terrible/frightful and both can intensify adjectives/verbs in colloquial use.
- This is a low-discrimination pair for A2/B1 unless the task focuses on register or fixed collocations.

`schrecklich`

- Simple German definition: Sehr schlimm oder unangenehm; manchmal auch als Verstärkung.
- Russian sense cue: ужасный / ужасно.
- Examples:
  - Das war ein schrecklicher Film.
  - Ich bin schrecklich müde.
- Progressive hint: Kann Angst/Schrecken meinen oder einfach "очень".

`furchtbar`

- Simple German definition: Sehr schlimm, sehr stark oder als Verstärkung.
- Russian sense cue: ужасный / ужасно, часто очень сильное отрицательное значение.
- Examples:
  - Der Sturm war furchtbar.
  - Das ist furchtbar nett.
- Progressive hint: Oft sehr schlimm oder umgangssprachlich "очень".

Contrast questions:

| Question | All valid answers | Why |
|---|---|---|
| Der Sturm war ____. | `furchtbar`, `schrecklich` | Both acceptable. |
| Ich bin ____ müde. | `schrecklich`, `furchtbar` | Both intensifiers. |
| Eine ____ Nachricht. | `schreckliche`, `furchtbare` | Both acceptable. |

Recommendation: include this only as an example of "both valid; do not force one answer", not as a priority contrast drill.

## Historical Confusion Candidates Needing Separate Validation

These are actual wrong-answer patterns from live reviews, but they need dictionary/context validation before becoming teaching material:

| Historical pattern | Evidence type | Suggested handling |
|---|---|---|
| `wischen` vs `den Boden sauber machen` | 2 wrong answers matched a known phrase card | Teach broad action vs specific floor-cleaning phrase; likely accept if prompt is broad "мыть пол", not if target is "wipe". |
| `entfernen` vs `entsorgen` | 2 wrong answers plus related environmental verbs | Contrast remove/delete vs dispose properly. Good candidate for a mini-set with `zerstören`, `wiederverwerten`, `abschaffen`. |
| `entsorgen` vs `zerstören` | 2 wrong answers | Not synonyms. Explain disposal vs destruction. |
| `sich bewegen` vs `bewegen` | 1 wrong answer | Grammar/reflexive transitivity, not synonymy. |
| `leiden an` vs `leiden unter` | live duplicate cue, both reviewed correctly | Good grammar/preposition contrast: illness/state vs circumstances/burden. |
| `melden` vs `mitteilen` | live duplicate cue `сообщать`, no proven error | Candidate only; validate contexts before use. |

## UX Edge Cases To Cover

- Ambiguous Russian cue: `доступный` alone cannot force `zugänglich`; `verfügbar`, `vorhanden`, `erhältlich`, `offen`, or `geöffnet` may be valid depending on context.
- Accepted synonym in recall/cloze: show "your variant fits" and keep target SRS neutral unless the target was explicitly reproduced in a visible recall task.
- Explicit target not used: if prompt says "Use `zugänglich`" and the learner writes a semantically correct sentence without it, mark meaning valid but condition unmet; offer one voluntary retry without double penalty.
- Inflection: accept correct forms such as `zugängliche`, `verfügbaren`, `erhält`, `bekommt`, `ruht ... aus`; do not compare raw lemmas only.
- Multi-answer buttons: if `Datenbank ... für alle` has both `zugänglich` and `verfügbar`, both button indices must be valid in the snapshot.
- Hints: once an extra hint reveals distinguishing sense or first letters, a correct answer should be educational and SRS-neutral unless the product explicitly wants hint-weighted progress.
- Defective generated task: if the hidden target is contradicted by context or multiple answers are valid but only one is stored, finish neutral and mark for review.
- Legacy task snapshots: old tasks without objective/visible target should be graded by visible conditions only; no hidden lemma requirement.
- Session loop: neutral outcomes must not immediately re-serve the same card indefinitely. Allow at most one voluntary follow-up in-session, then suppress until a later session.
- Feedback wording: avoid "expected X" when another natural answer was accepted. Use "another option" or "with the target word".

## Semantic Grading Cases

| Case | Prompt | Learner answer category | Expected result | SRS |
|---|---|---|---|---|
| Valid synonym | "Der Garten ist für Besucher ____." target `zugänglich` hidden | `geöffnet`/`offen` | Accepted as context-valid; explain `zugänglich` focus if needed | Neutral for `zugänglich` unless reproduced |
| Underdetermined prompt | "Das Produkt ist wieder ____." | `verfügbar`, `zugänglich` in an online-access scenario, `erhältlich`, `auf Lager` | Do not reject solely for hidden target; add stock/online-access cue or make neutral | Neutral unless the visible target was independently reproduced |
| Target not used when explicit | "Use `zugänglich`: say the site is open to visitors." | "Das Gelände ist für Besucher geöffnet." | Meaning valid, explicit target unmet; offer retry | Neutral |
| Inflection | "Nutze `verfügbar`: freie Termine." | "Es sind noch Termine verfügbar." / "verfügbare Termine" | Accepted | Practice-only success for `target_usage`; do not count as SRS recall success |
| Vague Russian cue | "Скажи по-немецки: доступный." no context | `verfügbar` or `zugänglich` or `vorhanden` | Do not reject solely for hidden target; ask for context or make neutral | Neutral |
| Multi-answer contrast | "Die Datenbank ist für Mitarbeitende ____." choices `verfügbar`, `zugänglich` | Either | Accepted; feedback names focus | Neutral or educational unless target-specific |
| Preposition contrast | "Nächsten Monat beginnen die Ferien. Ich freue mich ____ die Ferien." | `auf` | Accepted | Success for visible prep/card |
| Preposition wrong | "Das Geschenk liegt schon auf dem Tisch. Sie freut sich ____ das Geschenk." | `auf` | Rejected with time/sense explanation | Normal failure if visible contrast |
| Register overlap | "Ich bekomme/erhalte eine Nachricht." | either verb | Accepted if grammar correct; feedback on register | Neutral if hidden target not reproduced |
| LLM unavailable | any semantic free-text task | no reliable verdict | no user-facing rejection | Neutral/no review |

## Pilot Size Recommendation

Recommended first pilot:

1. `verfügbar` / `zugänglich`.
2. `zugänglich` / `vorhanden`.
3. `benutzen` / `verwenden`.
4. `beginnen` / `anfangen`.
5. `sich erholen` / `ausruhen`.
6. `vorschlagen` / `anbieten`.
7. `sich freuen auf` / `sich freuen über`.
8. `bekommen` / `erhalten`.
9. Optional low-priority "both valid" pair: `schrecklich` / `furchtbar`.

Do not expand beyond this until the historical confusion candidates are validated with contexts and source URLs. The goal is quality of contrast and fair grading, not count.
