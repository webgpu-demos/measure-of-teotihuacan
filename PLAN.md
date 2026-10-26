# The Measure of Teotihuacan

A plan to test whether the city was built with a shared unit of length, using excavation CAD, lidar and AI.

Generated from `docs/story.js`, the same storyboard that drives the film (`docs/film.mp4`, 5:30) and the walkthrough app (`docs/index.html`). Edit the storyboard, not this file.

## The plan in one paragraph

Teotihuacan’s lidar is a poor fit for “find the lost city”: the city is known, and most of the valley floor has been rebuilt on top of it along the same orientation. The excavation team’s 3D AutoCAD plans are a better starting point for a different question: did the builders share a standard unit of length? The plan turns the drawings into a large, source-linked set of dimensions, has the archaeologist tag them blind, freezes and pre-registers the dataset, tests named hypotheses with a cosine quantogram, Monte Carlo thresholds and Bayesian model comparison, and has Claude skeptic agents attack every result. The lidar is kept in an encrypted, fingerprinted vault, processed into a bare-earth surface and grid-aligned lines, classified ancient or modern with a model trained on the team’s own ground-checked map, and measured for a second, city-scale test. Claude builds, reads, remembers and doubts, but people make every call, it never sees coordinates, and the final statistics run without any AI. Every outcome is publishable because the test is fixed in advance.

## Steps

### 1. Teotihuacan · Valley of Mexico: A planned city

*Film 0:00–0:22*

> Nearly 2,000 years ago, about 100,000 people lived in this city.
> Its builders laid it out on one grid, turned 15.5° east of north.
> They planned in straight lines and right angles. So how did they measure?

Teotihuacan was one of the largest cities of the ancient Americas, and its streets, pyramids and apartment compounds share a single orientation, 15.5° east of astronomical north. A city planned that tightly invites a question about how its builders measured.

- Core city of about 20 km² with roughly 100,000 people at its height.
- An orthogonal grid oriented 15.5° east of north runs through monuments, streets and housing.
- The project asks whether that planning extended to a shared unit of length, and tests it properly.

### 2. The problem: A known city under a living one

*Film 0:22–0:46*

> In 2015, airborne lidar mapped 165 km² of the valley.
> About 60% of that ground has been reshaped in modern times.
> Many of the new fields and roads still follow the ancient grid.
> This is not a lost city waiting to be found. It is a known city under a living one.

The "scan public lidar for lost cities" approach works where ruins sit under forest on otherwise untouched ground. Teotihuacan is the opposite case: the city is known, and most of the valley floor has been rebuilt on top of it, often along the same orientation.

- The 2015 lidar survey covers about 165 km², more than four times the area of the classic foot survey.
- Ground classification: about 60% modern-made, 22% ancient, 3% mixed, 15% natural.
- Modern property lines often inherit the 15.5° grid, so orientation alone cannot separate old from new.
- Against walking survey, lidar under-counted features by about 31%. It complements survey; it does not replace it.
- Mining since 2015 has already removed about 205 recorded features, so the record is shrinking.

### 3. The question: Did they share a unit of length?

*Film 0:46–1:11*

> So ask a different question: not where, but how it was designed.
> Did its builders share a standard unit of length?
> Proposals include 83 cm, about 80.5 cm and about 58 cm. Or none at all.
> After three decades, there is still no consensus.

Several units have been proposed from hand-picked dimensions of major monuments, and none has been tested against a large, independent set of measurements with a pre-declared statistical procedure. That gap is the paper.

- Each proposal becomes a named hypothesis, fixed before any data is analysed.
- 83 cm and 80.5 cm differ by only 3%, so the study has to be precise enough to tell them apart.
- A related claim, that key distances count calendar numbers such as 260 and 73, gets its own test in Step 6.

### 4. Step 1 · Read: Start from the excavation drawings

*Film 1:11–1:26*

> The evidence may already exist, in 3D AutoCAD plans from the excavations.
> Step 1: convert each drawing, keep every layer, and rotate it onto the 15.5° grid.

The excavation team’s AutoCAD plans hold thousands of measured edges that nobody has analysed as a population. Step 1 turns them into clean geometry without losing anything the drafters encoded.

- Convert DWG to DXF with the free ODA File Converter, then read geometry and layers in Python with ezdxf.
- Handle lines, polylines, 3D faces and meshes; export any ACIS solids to mesh from AutoCAD first.
- Rotate every drawing into city-grid coordinates (15.5° east of north) so "length" and "width" mean the same thing everywhere.
- Map layer names to element types (wall, stair, talud, tablero, floor). Claude proposes the mapping; the archaeologist confirms it.

Who: Engineer, Claude

### 5. Step 2 · Extract: Pull out every design dimension

*Film 1:26–1:43*

> Step 2: software pulls out every dimension a builder would have set out.
> Wall thicknesses. Room widths. Stair treads. Talud and tablero heights.
> Each number links back to the line it came from.

Earlier studies chose which dimensions to measure. Here a program proposes every candidate, so the dataset is large and nobody can be accused of picking numbers that fit.

- Pair parallel faces to get thicknesses, room and corridor widths; take segment lengths for platform sides.
- Read stair treads, risers and widths, and talud and tablero heights, from the 3D geometry.
- Each record keeps its source: drawing, layer and entity handle, so any number can be checked against the plan.
- Block-sized dimensions are kept apart from building-sized ones, since small elements reveal units more reliably.

Who: Engineer, Claude

### 6. Steps 3–4 · Curate & freeze: Expert judgment, applied blind

*Film 1:43–2:01*

> Step 3: the archaeologist tags each one as original, restored or eroded, and by phase.
> Blind. Nobody sees which unit fits while tagging.
> Step 4: freeze the data, code and hypotheses, and pre-register them.

Only someone who excavated these buildings can say which faces are ancient, which were rebuilt in the 20th century, and which construction phase a wall belongs to. Doing that blind, then freezing the result, is what makes the test credible.

- A browser-based 3D viewer shows each candidate dimension on the plan; tagging is a few clicks.
- Tags: original, restored or eroded; construction phase; element type; confidence.
- Blind mode hides values and never shows them in any candidate unit.
- The frozen measurement set, analysis code and hypotheses are posted to OSF before the test runs.

Who: Archaeologist, Students

### 7. Step 5 · Test: Hunt for the quantum

*Film 2:01–2:24*

> Step 5: wrap every length around a circle one candidate unit long.
> If they share that unit, they bunch up at one point.
> Sweep from 30 cm to 2 m. A real unit rises above the noise.
> Its half-unit echoes too, and the analysis has to decide between them.

The core statistic is Kendall’s cosine quantogram, invented in 1974 to test claims of a "megalithic yard". Monte Carlo runs set the bar a peak must clear, and a Bayesian model comparison weighs one unit, two units and no unit against each other.

- Quantogram score for a candidate unit q: φ(q) = √(2/N) · Σ cos(2πXᵢ/q).
- Noise threshold: stretch or shrink each length by up to 15% at random, rescan, repeat hundreds of times.
- Cross-check against the published CQArchaeo package so the result does not rest on our code alone.
- Run per structure and per construction phase to see whether the unit holds steady or drifts.

Who: Engineer, Claude

### 8. Step 6 · Attack: Try to break every result

*Film 2:24–2:43*

> Step 6: AI skeptic agents try to explain each result away.
> Drafting rounding. Modern restoration. Stairs sized to feet. Lucky calendar numbers.
> Each objection becomes a check that runs before anyone believes the peak.

Metrology has a long history of people finding the unit they hoped for. Separate Claude agents are told to argue against each result; each objection they raise is turned into a concrete check that the pipeline runs.

- Skeptics see the result and the method, never the hoped-for answer.
- An objection only counts once it names a test that could confirm or rule it out.
- The archaeologist decides which objections are archaeologically plausible.

Who: Claude, Archaeologist

### 9. Step 7 · Store the lidar: One vault, one source of truth

*Film 2:43–3:06*

> Step 7: store the lidar like evidence.
> The raw point clouds stay on an encrypted lab drive, never on a public cloud.
> Every tile is saved in an open format and fingerprinted, so each result can name the exact data it used.
> Only derived numbers ever leave the vault.

The 2015 lidar is part of Mexico’s cultural patrimony: access runs through INAH and the data is treated as sensitive. So the first lidar task is custody, not analysis. A well-kept vault also makes the science reproducible, because every number can be traced to the exact tiles and settings that produced it.

- One working copy, on an encrypted drive or lab server with a named access list. No public cloud, no shared drives.
- Point clouds as COPC (cloud-optimized, compressed LAZ); surfaces as Cloud-Optimized GeoTIFF. Both are open formats that PDAL, GDAL and QGIS read directly.
- A manifest records a SHA-256 fingerprint for every tile and every derived raster, plus the processing settings. The manifest, not the data, lives in version control.
- What may leave the lab: lengths, angles, counts and summary statistics. Never coordinates, point clouds or imagery. Check this against the permit before starting.

Who: Engineer, Archaeologist

### 10. Step 8 · Process the lidar: From points to lines

*Film 3:06–3:32*

> Step 8: turn millions of points into a bare-earth surface.
> Ground filtering strips away houses and trees. Relief models make low walls and terraces stand out.
> A line detector then finds every edge that runs with the city’s grid.
> And a classifier trained on the team’s own ground-checked map sorts ancient lines from modern ones.

Processing turns the vault into measurable features in four stages. The last stage is where the existing map pays off: the 2021 survey hand-mapped over 7,000 features and checked many of them on foot, which makes it a ready-made training and test set.

- Ground: start from the delivered ground classification; re-run PDAL’s SMRF or CSF filter where modern buildings confuse it, and log every parameter in the manifest.
- Surfaces: a 0.5 m bare-earth DEM, then local relief, sky-view factor and multi-directional hillshade, the same family of views the 2021 study used, plus 0.5 m contours for low features.
- Lines: edge and line-segment detection on the relief model, keeping segments within 3° of the 15.5° orientation and merging collinear pieces.
- Classification: a segmentation model trained on the mapped features (1,061 architectural features and 6,314 terraces), tested on ground-verified areas it never saw. Low-confidence lines go to the archaeologist.

Who: Engineer, Claude

### 11. Step 9 · Measure the city: The same test, city-wide

*Film 3:32–3:49*

> Step 9: measure the city.
> Take the ancient lines and measure the gaps between parallel walls, from 10 to 500 m.
> Then run the same test, and ask whether streets and buildings share one unit.

The messy lidar becomes useful once the question is statistical. Thousands of grid-aligned edges give a second, independent dataset at a scale the excavation plans cannot reach.

- Project the classified ancient lines onto the grid axes and measure perpendicular spacing between parallel features.
- Run the quantogram from 10 to 500 m, with the same Monte Carlo threshold as the building-scale test.
- Compare the two scales: a shared unit should show up in both, or in clean multiples.
- Bonus measurement: each compound’s own orientation, to see whether the grid drifted across the city or over time.

Who: Engineer, Archaeologist

### 12. What the AI brings: Four jobs for Claude

*Film 3:49–4:20*

> So what does the AI actually bring?
> It builds: the CAD reader, the lidar pipeline and the statistics, each with tests.
> It reads: Spanish layer names, field notes and old reports become tidy tables.
> It remembers: every published dimension of the city, gathered into a second dataset.
> And it doubts: skeptic agents attack each result before a reviewer can.

The AI is not a black box that decodes the city. It is a fast, tireless colleague that does four specific jobs, each with a human check at the end. Together they let two people run a project that would normally need a small lab.

- Build: writes and tests the code for every step (ezdxf, PDAL and GDAL wrappers, extraction, statistics, figures), including training and evaluating the lidar classifier, always starting from synthetic data where the right answer is known.
- Read: proposes layer-to-element mappings, pulls phases and conditions out of field notes, turns legacy reports into structured rows, and drafts the methods and a Spanish summary for INAH and local partners.
- Remember: extracts every published Teotihuacan dimension, with its citation, into an independent comparison dataset.
- Doubt: runs the skeptic round in Step 6 and reports where the classifier and the statistics are least certain.

Who: Claude

### 13. Where the AI stops: People decide, AI assists

*Film 4:20–4:42*

> Just as important is where the AI stops.
> It never decides what is ancient. The archaeologist confirms every tag it proposes.
> It never sees coordinates or imagery, only lengths and labels.
> Every prompt and script is logged, and the final statistics run without any AI at all.

A reviewer will ask what the AI did. The answer has to be short and checkable: it proposed, people decided, and the test itself is classical statistics that anyone can rerun.

- Every AI proposal is a suggestion with a status: proposed, confirmed or rejected, and who decided.
- Claude never receives coordinates, point clouds, imagery or anything that locates a feature.
- Prompts, model versions and scripts are logged with the manifest, so the whole chain can be audited.
- The quantogram, Monte Carlo and model comparison are ordinary code that runs without any AI, so the result does not depend on a model.

Who: Archaeologist, Engineer

### 14. Who does what: Three roles

*Film 4:42–4:58*

> The archaeologist knows which stones are original, and what an answer would mean.
> The lidar engineer owns the vault and the pipeline.
> And Claude does the heavy lifting in between.

Each role covers what the others cannot. The archaeologist holds the ground truth and the permits, the engineer holds the data and the build, and Claude multiplies both.

- Archaeologist: original versus restored, construction phases, permits, interpretation, and the final say on every tag.
- Lidar and 3D engineer: the vault, the processing pipeline, the 3D viewer and the reproducible runs.
- Claude: code, reading, literature, classification and skeptic review, all under the guardrails in the previous step.

### 15. What we publish: Every outcome is a paper

*Film 4:58–5:13*

> 83 cm holds. A different unit fits. The unit drifts over time. Or there was none.
> Because the test is fixed in advance, every result is publishable.

Pre-registration turns a yes-or-no gamble into a guaranteed publication: a clean null result is as informative as a confirmation, and the toolkit is a paper of its own.

- Main paper: a pre-registered test of Teotihuacan measurement units from 3D excavation data, aimed at Journal of Archaeological Science, Latin American Antiquity or PLOS ONE.
- Methods paper: the open-source toolkit (with no site data), aimed at the Journal of Computer Applications in Archaeology.
- Talks: the SAA Annual Meeting and CAA.

### 16. First steps: Start small, then scale

*Film 5:13–5:30*

> Weeks 1–2: prove the method on synthetic buildings.
> Weeks 3–6: one structure, end to end.
> Then the full corpus, the test, and the city.

The first two weeks need nothing from the excavation archive except one sample drawing, and they answer the most practical question: how many curated measurements does the test need?

- Weeks 1–2: synthetic buildings with a planted unit, a pure-noise control, and a building drawn in feet (the pipeline should find 30.48 cm). Output: a power analysis. In parallel, set up the lidar vault and manifest.
- Weeks 3–6: one structure from DWG to tagged measurements in the viewer; lidar ground check, surfaces and line detection.
- Weeks 7–10: full corpus curation, pre-registration, the test, the skeptic round; train and test the lidar classifier on ground-verified areas.
- Weeks 11–16: the city-scale test, the cross-scale comparison and the first draft.

## Hypotheses (fixed before the test)

| Hypothesis | Note | Detail |
|---|---|---|
| 83 cm | proposed 1993 | Read from balustrade widths, stair widths and sculpture spacing at the Feathered Serpent Pyramid, then applied to city-scale distances. |
| ≈80.5 cm | earlier estimate | An earlier figure from work on measurement units and building axes. Only 3% shorter than 83 cm. |
| ≈58 cm | later proposal | A smaller unit proposed in later comparative metrology. |
| Drifting | changed over time | A unit that shifts between construction phases, which only phase-tagged data can detect. |
| None | no shared unit | Dimensions set by materials, bodies and local practice rather than a common unit. |

## Skeptic checks

| Objection | Check that runs | What would change our mind |
|---|---|---|
| Drafting rounding? | Scan the decimals for snap-to-grid | If the peak survives on total-station points alone, rounding is not the cause. |
| Restored in the 1900s? | Drop restored faces, rerun | A peak carried by restored faces measures the restorers, not the builders. |
| Stairs sized to feet? | Test stairs on their own | Treads and risers follow human bodies everywhere; the unit has to appear in walls and platforms too. |
| Lucky calendar numbers? | Simulate random-integer hits | Count how often random distances land near 260, 73 or 52 units, and require the real data to beat that rate. |
| Picked lengths that fit? | Automated extraction, blind tags | Every candidate dimension enters the dataset, and curation never sees the unit. |
| Half-unit echo? | Check odd multiples of the half | If odd multiples of 41.5 cm are common, the real unit is the half. |

## Roles

- **Archaeologist** (ground truth): Original vs restored, construction phases, permits, and what the answer means.
- **Lidar & 3D engineer** (the vault and pipeline): Lidar custody, processing and classification; the CAD reader and 3D viewer.
- **Claude** (speed and scrutiny): Builds, reads, remembers and doubts, always proposing and never deciding.

## Outcomes

- **83 cm holds.** A large, blind, pre-registered confirmation of a 30-year-old proposal.
- **A different unit fits.** A new estimate, with its uncertainty, from independent data.
- **The unit drifts over time.** Evidence that building practice changed between phases.
- **No shared unit.** A clean null that retires a long-running claim.

## Timeline

- **Weeks 1–2:** Synthetic buildings; set up the lidar vault
- **Weeks 3–6:** One structure end to end; lidar surfaces and lines
- **Weeks 7–10:** Full corpus, freeze, test, attack; train the classifier
- **Weeks 11–16:** City-scale test and the first draft

## Questions for the first call

- [ ] Which structures have AutoCAD plans, and are they 2D lines, 3D lines or meshes? What units and coordinate system?
- [ ] How were they made: total station, photogrammetry, or hand drawings digitized later? That sets the measurement error.
- [ ] Are layers organised by construction phase or element type?
- [ ] Which faces are restored or eroded, and who could mark them?
- [ ] Do the original measurement tables behind the 83 cm proposal still exist? They allow a replication before the independent test.
- [ ] Do the permits allow derived measurements (no coordinates) to be processed by a cloud AI service?
- [ ] Is everyone comfortable publishing whatever the test shows?
- [ ] Who on the team could spend a few hours a week curating?

## Sources

- [Humans as geomorphic agents: Lidar detection of the past, present and future of the Teotihuacan Valley, Mexico](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0257550), PLOS ONE, 2021
- [Worldview materialized in Teotihuacan, Mexico](https://www.cambridge.org/core/journals/latin-american-antiquity/article/abs/worldview-materialized-in-teotihuacan-mexico/AA453937CDB161024086890A7A31A71C), Latin American Antiquity, 1993
- [Deriving ancient foot units from building dimensions: a statistical approach employing cosine quantogram analysis](https://proceedings.caaconference.org/files/2001/62_Pakkanen_CAA_2001.pdf), CAA 2001
- [CQArchaeo: a Python package for cosine quantogram analysis and Monte Carlo simulations](https://www.archcalc.cnr.it/indice/PDF35.1/15_Lago_Cardarelli_Ialongo.pdf), Archeologia e Calcolatori, 2024
- [The metrological research of Machu Picchu: cosine quantogram analysis of 3D laser data](https://www.imeko.org/publications/tc4-Archaeo-2016/IMEKO-TC4-ARCHAEO-2016-22.pdf), IMEKO TC4 Archaeo, 2016
- [I went looking for lost cities with Claude](https://bakerbench.substack.com/p/i-went-looking-for-lost-cities-with), Substack, 2026
