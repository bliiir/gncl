# Model comparison

Regenerate with `uv run python -m gncl.eval`.

## This dataset's traps

Decides whether a model handles the conflicts the pipeline raises today.

Gold set: 15 pairs.

**Calibration.** 15 items means one case is 7 percentage points. A model ahead by a single case has not been shown to be better. This table filters obvious failures; it does not rank close finishers. The baseline rows are what a model that ignores the question scores.

| Model | Accuracy | Correct | p50 | p95 |
|---|---|---|---|---|
| gemma4:12b | 93% | 14/15 | 2.78s | 4.51s |
| gemma4:26b | 93% | 14/15 | 1.54s | 8.18s |
| gemma4:12b-mlx | 47% | 7/15 | 3.67s | 11.29s |
| _baseline: always yes_ | 53% | 8/15 | - | - |
| _baseline: always no_ | 47% | 7/15 | - | - |

**gemma4:12b** got these wrong:

- `Fredrik` vs `Tone Fredriksen` (substring): expected False, said True. The first name 'Fredrik' matches the given name in 'Tone Fredriksen'. While 'Tone' is a common Norwegian first name and 'Fredrik' is often used as a middle or second name, in many booking systems, only one part of a full name is recorded. Given they share the same cabin and date, it is highly likely to be the same individual.

**gemma4:26b** got these wrong:

- `Camilla Strnad` vs `Camilla Strand` (typo): expected True, said False. The surnames 'Strnad' and 'Strand' are different. While they are phonetically similar, the instructions state that different given names with a shared surname are different people; by extension, different surnames imply different individuals unless there is clear evidence of a typo, but in identity matching, a change in surname spelling is treated as a different person.

**gemma4:12b-mlx** got these wrong:

- `Beth` vs `Elisabeth Holm` (diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Andy` vs `Anders Moe` (diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Mikael Svnesson` vs `Mikael Svensson` (typo): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Camilla Strnad` vs `Camilla Strand` (typo): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Fredrik` vs `Fredrik Lund` (first name): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Bjorn Kristiansen` vs `Bjørn Kristiansen` (transliteration): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Freja Sorensen` vs `Freja Sørensen` (transliteration): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Kristine` vs `Kristine Andersen` (first name): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"


## Swedish short forms

Decides whether it survives the intended population. This is the set where no threshold works: lasse/lars, kalle/karl and nisse/nils are 0.67 and the same person; stina/stig is 0.67 and is not; pelle/per is 0.50 and is.

Gold set: 17 pairs.

**Calibration.** 17 items means one case is 6 percentage points. A model ahead by a single case has not been shown to be better. This table filters obvious failures; it does not rank close finishers. The baseline rows are what a model that ignores the question scores.

| Model | Accuracy | Correct | p50 | p95 |
|---|---|---|---|---|
| gemma4:12b | 82% | 14/17 | 2.79s | 7.37s |
| gemma4:26b | 82% | 14/17 | 1.59s | 8.26s |
| gemma4:12b-mlx | 47% | 8/17 | 3.63s | 11.58s |
| _baseline: always yes_ | 53% | 9/17 | - | - |
| _baseline: always no_ | 47% | 8/17 | - | - |

**gemma4:12b** got these wrong:

- `Pelle` vs `Per Sjoberg` (sv diminutive): expected True, said False. While 'Pelle' is a common nickname for 'Per' in Swedish, the first name provided ('Pelle') lacks a surname to confirm it matches 'Per Sjoberg'. Without a shared surname or additional identifying information, they cannot be confirmed as the same person.
- `Malin Ohlsson` vs `Malin Olsson` (sv near-miss): expected False, said True. The names 'Ohlsson' and 'Olsson' are common variations of the same Swedish surname, and 'Malin' is a consistent given name. The difference is likely a minor spelling variation.
- `Lasse Eriksson` vs `Lasse Ericsson` (sv near-miss): expected False, said True. The names 'Eriksson' and 'Ericsson' are common spelling variations of the same Scandinavian surname, and the given name 'Lasse' is identical.

**gemma4:26b** got these wrong:

- `Lasse` vs `Lars Eriksson` (sv diminutive): expected True, said False. Lasse is a common diminutive for Lars in Scandinavian languages, but without a shared surname or further identifying information, 'Lasse' and 'Lars Eriksson' cannot be definitively identified as the same person; they could be different individuals with different surnames.
- `Malin Ohlsson` vs `Malin Olsson` (sv near-miss): expected False, said True. The names are identical except for a single character difference in the surname ('Ohlsson' vs 'Olsson'), which is a common typographical error or spelling variation of the same Swedish surname.
- `Lasse Eriksson` vs `Lasse Ericsson` (sv near-miss): expected False, said True. The names differ only by the spelling of the surname (Eriksson vs Ericsson), which is a common orthographic variation in Scandinavian names.

**gemma4:12b-mlx** got these wrong:

- `Lasse` vs `Lars Eriksson` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Kalle` vs `Karl Nyberg` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Nisse` vs `Nils Berg` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Stina` vs `Kristina Lund` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Micke` vs `Mikael Svensson` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Pelle` vs `Per Sjoberg` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Bettan` vs `Elisabeth Holm` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Sussie` vs `Susanne Ahlberg` (sv diminutive): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
- `Goran Sjoberg` vs `Goeran Sjoeberg` (sv transliteration): expected True, said False. unusable response: non-JSON: ```json
{"type": "object", "properties": {"same_person": {"type": "boolean"}, "confidence": {"type": "number"}, "reason"
