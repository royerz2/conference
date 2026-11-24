# BioMastermind

BioMastermind pairs newly published plant gene-expression papers and public data deposits with an open research question. It drafts a falsifiable comparison and shows the two source records side by side. It runs locally with Python 3.9+ and uses the standard library.

## Open the dashboard

Unzip the project, enter the `biomastermind` directory, and run:

```sh
python3 biomastermind.py serve
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The first screen shows an offline fictional example. **Run live scan** searches Europe PMC and Zenodo with the public keywords. Enter a research question, 2–8 comma-separated public terms, and a lookback of 1–365 days. The scan sends the first two terms to each repository, then uses all terms and the local question to rank the records. A wider lookback can help on sparsely published topics. The report is kept in local SQLite and can be exported as JSON.

A scan reads up to 45 paper records and 25 Zenodo datasets and reports the number available. PubMed is the fallback when Europe PMC is unavailable. The dashboard distinguishes an unavailable source from an empty search. Each candidate is a proposed comparison based on titles and abstracts or deposit descriptions, with file names when Zenodo lists them. Confirm the measurements, study design, and sample independence before treating a candidate as testable. Reports contain public text excerpts and source links.

## Optional local model

Start Ollama on the same computer. Enter the name of an installed model in the dashboard, then run a scan. BioMastermind asks it to draft the top hypothesis and supporting comparison from the retrieved records. The app uses a template when the model is omitted or returns an unusable response. The watch question is stored locally. Public repository requests contain only keywords and dates. When using Ollama, select a locally downloaded model for local inference.

## Repeat the watch

Run once with the watch saved from the dashboard:

```sh
python3 biomastermind.py scan
```

Repeat every 24 hours while the process is running:

```sh
python3 biomastermind.py scan --interval-hours 24
```

The command prints one JSON report per scan. The dashboard shows the latest saved scan on reload. If using a system scheduler, schedule the single-run command and let the scheduler handle timing. All commands use the same SQLite file beside the app by default. Use `--db /path/to/watch.sqlite3` before a subcommand to switch databases. Stop the server or repeating scan with Ctrl+C.

## Check a prediction using prepared data

After verifying the study design, prepare a CSV with `sample,condition,value`. Use one comparable numeric summary per independent biological replicate, such as a pre-specified gene-set score. Choose the expected direction before inspecting the candidate cohort. For the fictional dashboard example:

```csv
sample,condition,value
leaf_1,before,0.20
leaf_2,before,0.35
leaf_3,after,0.55
leaf_4,after,0.65
```

```sh
python3 biomastermind.py compare scores.csv --control before --treatment after --expect higher
```

The command reports group counts, means, the difference, and whether the observed direction matches. This is a descriptive check for independent replicates. Paired studies, covariate adjustment, and uncertainty estimates require a separate analysis.

## How candidates are ranked

The first pass looks for signs that both records discuss gene-expression data, then uses keyword overlap and terms from the research question. It keeps the newest returned version per Zenodo concept and skips pairs with obvious shared DOIs, sequencing accessions, or nearly identical titles. It also skips pairs with overlapping author surnames and substantial shared terminology, which can omit independent cohorts from the same lab. Pairs with different visible identifiers can still reuse samples. The rolling window uses paper publication dates and Zenodo record creation dates. After consecutive successful scans for the same watch, the dashboard flags pairs that were absent from the previous shortlist. Review each linked record and its files to judge feasibility.

- [Europe PMC REST documentation](https://europepmc.org/RestfulWebService)
- [Zenodo REST documentation](https://developers.zenodo.org/)
- [Ollama generate API](https://docs.ollama.com/api/generate)
