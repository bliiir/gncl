# Traps in the case data

Every trap below checked against the three CSVs in `case/`. Measured rates live in `docs/DATA_ANALYSIS.md`.

## Big

**Nationality is not phone country code.** BookIT has `nationality` (SE/NO/DK). HubSpot has phone prefix (+46/+47/+45). Agree on 19% of known-true pairs, 33% of non-matching pairs. Anti-correlated. Not a signal. Do not use it to repair nationality either.

**Two Anna Larsen. Cabins differ by one digit.** BK1001, cabin 4021, DK, 07-11..07-14, has email. BK1021, cabin 4022, SE, 06-11..06-16, no email. HubSpot carries both (HS201 with email, HS221 without). LS Retail has `Anna` at 4021 and at 4022. Only first name in LS Retail mapping to two cabins. Fuzzy cabin merges two people.

**Cabin is not a key.** Cabin 6208 holds Nils Berg (06-28..07-05) and Marcus Berg (07-19..07-24). Same surname. Cabin + stay window is the key.

**Surname clusters sit in adjacent cabins.** Berg x3 (6208, 6208, 6209). Holm x2 (Elisabeth 3067, Linnea 3068). Larsen x2 (4021, 4022). Surname matching and cabin matching both fail on the same rows.

**Nobody travels together.** The obvious read of those clusters is families in neighbouring cabins. Measured, it does not hold. All five same-surname pairs are time-disjoint, and zero adjacent-cabin bookings overlap in time, while all 32 bookings overlap with some other booking. The adjacency is planted.

**LS Retail records first name only, sometimes a nickname.** `Beth` at 3067 = Elisabeth Holm. `Andy` at 3145 = Anders Moe. Edit distance does not reach either.

**Both surname-bearing systems misspell the same surname, differently.** BookIT `Mikael Svnesson`, HubSpot `Mikael Sevnsson`, true `Svensson`. BookIT `Camilla Strnad`, HubSpot `Camilla Stradn`, true `Strand`. Name-to-name fuzzy match is weak between two corruptions. No third opinion available: LS Retail holds first name only (`Mikael` at 6112, `Camilla` at 6077), so two sources out of three carry the surname and both are wrong. Correct spelling sits in the email local part.

**Emails go blank on the ambiguous rows.** 6 blank in BookIT, 7 in HubSpot. Blank on both sides for: Gustav Ahlberg, Erik Solberg, Nils Berg, Thomas Berg, Anna Larsen (BK1021/HS221). Not coincidence.

**Some records must stay unmatched.** CRM only, never sailed: Oskar Lindberg, Pernille Rasmussen, Aleksander Hoff. BookIT only, no CRM: Signe Mortensen, Kjell Bakken, Ida Truelsen, Ragnhild Vik. Ragnhild Vik has no email, no CRM row, and zero transactions against cabin 2201.

## Small

**Cabin dropped in 4 transactions** (Tone x2, Sara, Fredrik). Sara Jensen also has no cabin in BookIT. Her only signal is first name + date.

**Stay window is inclusive at both ends.** All 53 cabin-bearing transactions fall inside a stay. 14 land exactly on check-in or check-out day. Half-open `[in, out)` drops 6.

**Brief promises inconsistent date formats. There are none.** All dates in all three files are ISO `YYYY-MM-DD`. Zero exceptions. No parser needed.

**Nordic folding required.** Names carry Bjorn/Bjørn, Sorensen/Sørensen; emails carry `bjorn.kristiansen`, `freja.sorensen`. Local part does not always rebuild the name: `m.karlsson` = Maja Karlsson, `pernille.r` = Pernille Rasmussen.

**Email is a clean exact key here.** No email repeats inside a file. No email is shared by two people. Covers 21 of ~35 people. Nothing else is exact.

**`amount` and `store` carry no identity.** Usable only inside LS Retail, where cabin + window already resolves 53 of 57 rows.

## The open call

Anna Larsen: two people, or one person with two bookings? Different cabin, different dates, different nationality, two HubSpot rows all point to two. Nothing proves it. The one signal that could have split them (marketing contact date) measures at 1.08x lift. Brief asks for calibrated confidence over false certainty, so this ships flagged, not decided.

A third Anna Larsen cannot be ruled out either, and the pipeline should not claim otherwise. BookIT carries one name per booking with no party size, so a second person in a cabin leaves no trace unless they buy something under their own name, which never happens here. Searched every source for a third by substring, by surname similarity down to 0.70, by first name and by email address. Two, in every direction. That is what the sources record, and it is a weaker statement than saying only two sailed.
