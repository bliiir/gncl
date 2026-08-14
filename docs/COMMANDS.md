# What the commands do

The four commands in the README, drawn. Every box is a real function; the file and the layer it belongs to are named where it helps.

## `gncl resolve`

The pipeline. Reads three CSVs, writes one.

```mermaid
flowchart TD
    CLI["gncl resolve"] --> CFG{".env present?"}
    CFG -- no --> STOP["ClickException:<br/>copy .env-example"]
    CFG -- yes --> SRC["CsvSource.frames()<br/>ports.py"]

    subgraph L0["L0 normalize - load.py"]
        SRC --> NORM["casefold, Scandinavian folding,<br/>closed date windows,<br/>name repaired from email local part"]
    end

    NORM --> BUILD["match.build()"]

    subgraph L12["L1 + L2 match - match.py"]
        BUILD --> BH["resolve_bookit_hubspot()<br/>exact email 0.98<br/>then repaired-name candidates,<br/>max-weight bipartite matching"]
        BUILD --> TX["attach_transactions()<br/>cabin + stay window 0.95<br/>first name + window 0.80"]
        BH --> G["nx.Graph:<br/>records are nodes,<br/>evidence is weighted edges"]
        TX --> G
        TX -.-> CONF["name_conflicts:<br/>captured name disagrees<br/>with the booking it links to"]
    end

    CONF --> AVAIL{"audit enabled and<br/>Ollama reachable?"}
    AVAIL -- no --> NOTRUN["audit = not run<br/>link kept, evidence says why"]
    AVAIL -- yes --> JUDGE["audit_conflicts()<br/>llm.py - cached on the<br/>normalized name pair:<br/>4 transactions, 2 calls"]

    subgraph L3["L3 audit - local model"]
        JUDGE --> VERDICT{"same person?"}
        VERDICT -- yes --> OK["audit = confirmed<br/>confidence unchanged"]
        VERDICT -- no --> BAD["audit = contradicted<br/>confidence capped at 0.50"]
    end

    G --> COMP["output.build_guests()<br/>one connected component = one guest<br/>confidence = weakest edge in it"]
    NOTRUN --> COMP
    OK --> COMP
    BAD --> COMP

    COMP --> BAND["join.review_band()<br/>accepted / review<br/>single source is accepted:<br/>no join to adjudicate"]
    BAND --> WRITE["join.write_outputs()<br/>37 columns, head first"]
    WRITE --> CSV[("out/guests.csv<br/>35 rows")]
    WRITE --> SUM["summary on stdout:<br/>20 deterministic, 10 rule_fuzzy,<br/>5 single source, 34/1"]
```

L4 is the absence of a step: a record that reaches the end unlinked is emitted as `single source` rather than forced into a pairing.

## `gncl show G-BK1021`

Reads the file the pipeline wrote. No matching happens here.

```mermaid
flowchart LR
    CLI["gncl show G-BK1021"] --> F{"out/guests.csv<br/>exists?"}
    F -- no --> STOP["run gncl resolve first"]
    F -- yes --> ROW["find the row"]
    ROW --> COLS["print every column"]
    ROW --> EV["split evidence on ' | '<br/>one line per piece"]
    EV --> CHAIN["cabin + stay window ...<br/>captured name 'Beth' disagrees ...<br/>exact email ...<br/>audited by gemma4:12b ..."]
```

## `gncl graph`

The same components the pipeline resolved, drawn. Small multiples, because the graph is 35 components of at most five nodes and a single force-directed blob would hide the one guest resting on a weak edge.

```mermaid
flowchart LR
    CLI["gncl graph"] --> P["the pipeline again,<br/>audit off by default:<br/>rules make the links"]
    P --> PANEL["one panel per guest:<br/>booking on the left,<br/>what it links to on the right"]
    PANEL --> W["weight written on every edge,<br/>teal deterministic, amber rule/fuzzy,<br/>red dot on a flagged guest"]
    W --> SVG[("out/graph.svg")]
    CLI -. "--guest G-BK1021" .-> ONE[("out/graph-G-BK1021.svg")]
```

Layout is semantic - edges only run BookIT to HubSpot and BookIT to LS Retail, so left-to-right says what the evidence is. It also makes the file deterministic, which a spring layout would not.

## `gncl ui`

One file, openable from disk, with nothing to fetch.

```mermaid
flowchart TD
    CLI["gncl ui"] --> R["ui.render(out)"]
    CSV[("out/guests.csv")] --> R
    LOGO[("gncl/static/<br/>gncl-logo-wide.svg")] --> R
    R --> BUILD["table + badges + stat tiles,<br/>CSS and JS inlined,<br/>CSV embedded as a data URI"]
    BUILD --> HTML[("out/index.html")]
    HTML --> OPEN["opens in the browser"]
    BUILD -.-> CHATOFF["chat tab renders its<br/>disconnected state: a file<br/>has no server to ask"]
```

## `gncl serve`

The same page, plus the API, plus a chat tab that can answer.

```mermaid
flowchart TD
    CLI["gncl serve"] --> AUTH{"GNCL_AUTH_USER<br/>and PASSWORD set?"}
    AUTH -- no --> STOP["refuses to serve<br/>unauthenticated"]
    AUTH -- yes --> UV["uvicorn: gncl.api:app"]
    UV --> LIFE["lifespan builds State once:<br/>the resolve pipeline, in memory,<br/>audit included"]
    LIFE --> READY["/health passes only<br/>once it can serve"]

    subgraph REQ["every request - basic auth, then a rate limit"]
        GUESTS["/guests?threshold=<br/>/guests/G-BK1021<br/>/review-queue<br/>/stats<br/>/raw/bookit"]
        PAGE["GET / - the same page,<br/>chat live if a key is set"]
        CHAT["POST /chat"]
    end

    READY --> GUESTS
    READY --> PAGE
    READY --> CHAT

    CHAT --> HIST{"history valid?<br/>alternating, max 20 turns"}
    HIST -- no --> E422["422"]
    HIST -- yes --> CTX["chat.context():<br/>measured facts computed in pandas,<br/>guests.csv, the 3 raw sources,<br/>the brief and the docs - ~63 KB"]
    CTX --> KEY{"ANTHROPIC_API_KEY?"}
    KEY -- no --> NULLA["usable: false<br/>says so, answers nothing"]
    KEY -- yes --> MODEL["hosted model"]
    MODEL --> ANS["answer, citing a guest_id<br/>or a file"]
```

The key never leaves the server: the browser posts a question, the server holds the credential. That is why the chat tab exists on `GET /` and not in the standalone file.

## Where the two models differ

```mermaid
flowchart LR
    subgraph P["pipeline - local, Ollama"]
        A["cost is linear in link count"] --> B["gemma4:12b, one narrow<br/>judgement against a schema"]
        B --> C["guest names never leave the box"]
    end
    subgraph U["chat - hosted"]
        D["cost is flat in dataset size"] --> E["open-ended questions<br/>about resolved output"]
        E --> F["tens of calls, human-paced"]
    end
```

Argued in `docs/SPECIFICATION.md` section 5.1. Both degrade in the open: with no model the audit reports `not run` and the chat says it is unconfigured, and neither silence can be read as a verdict.
