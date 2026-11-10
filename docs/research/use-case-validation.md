# zim-prospect: use-case validation

Desk research, 10 Nov 2026, on the use case only. Figures are from press and secondary sources; check before quoting.

## Verdict

**The use case is real but narrow.** It is not a "find gold" app for individual artisanal miners: 500 m is too coarse, and they need a licence to act on any target. The tool fits **desk screening and due diligence for people who acquire or evaluate ground**:

1. **Consultants and geologists** serving claim buyers, investors and juniors. This is the best first segment.
2. **Organised small-scale gold miners**, reached through syndicates and associations, who need a sampling plan for ground they already hold.

Start with **gold** and **chromite**. The biggest gaps are a **title and exclusion overlay** and **magnetics**. Free Canadian aeromagnetic data may exist; see section 4.

## 1. Who would use it

| Segment | Evidence | Their decision | Does it help? |
|---|---|---|---|
| Artisanal and small-scale (ASM) gold miners | About 1 million people, plausible range 0.5–1.5 million ([Herald](https://www.heraldonline.co.zw/mapping-a-million-lives-beneath-the-surface/)). They delivered **34.9 t of a record 46.7 t (about 75%)** to Fidelity Gold Refinery in 2025 ([AllAfrica](https://allafrica.com/stories/202602230368.html)). Regularisation deadline: 31 Dec 2026 ([SMM](https://news.metal.com/newscontent/104103697-smm-chromium-flash-zimbabwes-artisanal-mining-deadline-extension-comes-as-chrome-features-in-fresh-smuggling-count)). | Where to peg, which reef to follow | **Low** at 500 m: they work known districts. **Moderate** at 10–20 m on their own claim, with a sampling plan and offline maps. Low ability to pay; reach them via syndicates or the Zimbabwe Miners Federation. |
| Junior explorers | For example, Kavango is buying 45 Filabusi-belt claims from local holders ([Mining Technology](https://www.mining-technology.com/news/kavango-option-acquire-100-nara-gold-project-zimbabwe/)) | Where to apply for an exclusive prospecting order (EPO) or buy claims | **Moderate.** Regional screening; trusted only once it uses ZGS geology and magnetics. |
| Investors and claim buyers | Disputed titles are common ([Equity Axis](https://equityaxis.net/post/18848/2026/3/a-disputed-claim-in-a-country-full-of-them-zimbabwe-s-gold-rush-and-risks-investors-are-taking)) | Whether to buy a claim on offer | **High, if the tool can score a supplied claim polygon.** They would pay per report. |
| Consultants | The owner could be one | Producing a target report quickly | **High.** A productivity aid they can run themselves. |
| Government (ZGS, Ministry of Mines) | Better data; national airborne survey "on the cusp" ([FinGaz](https://fingaz.co.zw/2026/06/25/government-to-map-mineral-reserves/)) | Where to survey and promote ground | Low as a customer; useful as a data partner |
| NGOs and researchers (planetGOLD, ZELA) | Mercury and ASM governance ([UNEP](https://www.unep.org/gef/news-and-stories/technical-highlight/planetgold-zimbabwe-takes-aim-mercury-use-small-scale-gold)) | Where ASM activity and harm will spread | Low to moderate; grant-funded |

## 2. Legal and regulatory fit

**Current law: Mines and Minerals Act [Chapter 21:05]** ([FAOLEX](https://faolex.fao.org/docs/pdf/zim060737original.pdf), [Herald](https://www.heraldonline.co.zw/prospecting-for-minerals/))

- **Prospecting licence** from the Provincial Mining Director: holder over 18 and a permanent resident; lasts 2 years; cannot be sold.
- **Pegging.** One block per mineral class, after a notice of intention.
- **No digging without exclusive rights.** Minerals may be removed only for assay.
- **Closed land** (s31): private land, towns, and communal village land without the rural district council's consent.

**So a user may** visit a target on open land and take hand samples for assay. Digging needs a licence, a registered claim and, before mining, environmental (EMA) approval.

- **EPOs** cover up to 65,000 ha for 3 years. Automatic renewal stopped in 2023, and applicants' capacity is now reviewed ([Bulawayo24](https://bulawayo24.com/news/national/252651)). Coal and strategic minerals need special grants, which individuals cannot realistically get.
- **The Mines and Minerals Bill, 2025** was gazetted in June 2025. I found no evidence it has been enacted ([AllAfrica](https://allafrica.com/stories/202506260024.html)). It would:
  - replace EPOs with **Exclusive Prospecting Licences of 12 months and 40 ha**;
  - create a digital **Mining Cadastre Register**;
  - require landholder consent;
  - cap small-scale miners at 40 ha ([MMM Law](https://www.mmmlawfirm.co.zw/overview-of-the-provisions-of-the-mines-and-minerals-bill-2025/)).
- **Cadastre.** The e-cadastre has been delayed since 2021 and is now targeted for the end of 2026 ([FinGaz](https://fingaz.co.zw/2026/06/21/government-targets-year-end-for-e-cadastre-launch/)). Until then, **nobody can check online whether a target is already held.**
- **Lithium.**
  - Raw ore exports have been banned since 2022.
  - All raw-mineral and concentrate exports were suspended on 25 Feb 2026 ([Al Jazeera](https://www.aljazeera.com/news/2026/2/25/zimbabwe-imposes-ban-on-exports-of-all-raw-minerals-and-lithium-concentrate)).
  - Since April, licensed producers export under quotas, and concentrate exports are banned from January 2027 ([Engineering News](https://www.engineeringnews.co.za/article/zimbabwe-to-introduce-lithium-export-quotas-sets-conditions-for-resumption-of-shipments-2026-04-08)).
  - Artisanal lithium rushes at Mberengwa and Goromonzi were cleared ([Mining Index](https://www.miningindex.co.zw/?p=24026), [PRIO](https://www.prio.org/2023/03/renewable-energy-renewable-conflicts-how-a-lithium-rush-in-zimbabwe-is-raising-tensions/)).
- **Protected areas.** Mining has been banned in 10 national parks and in riverbeds since 2020 ([Miningmx](https://www.miningmx.com/news/energy/43610-zimbabwe-bans-mining-in-national-parks-after-hwange-coal-drilling-uproar/)).

**Risk.** Public greenfield coordinates could trigger rushes, claim invasions, park incursions and riverbed or mercury damage.

**Mitigations:**
- Mask parks, riverbeds and towns out of the rankings.
- Label targets "title unknown; check with the Provincial Mining Director".
- Keep greenfield coordinates off public pages.
- Unlock the zoom only for a supplied licence or claim area.
- Print the licence rules on every output.

## 3. Alternatives

| Alternative | Strength | Gap that zim-prospect fills |
|---|---|---|
| ZGS (maps, bulletins, advice and local geophysics for small-scale miners) | Authoritative geology and magnetics | Paper-based and slow; gives no ranked targets |
| KoBold, Earth AI | Proprietary data and their own drilling ([Skillings](https://skillings.net/the-frontier-blueprint-how-kobold-metals-uses-ai-to-locate-deep-copper-in-zambia/)) | Explore for themselves and sell no service; no Zimbabwe activity found |
| VRIFY DORA, ALS Geoanalytics (formerly GoldSpot) | Expert-supported subscriptions or consulting ([VRIFY](https://vrify.com/pricing), [ALS](https://www.alsglobal.com/en/News-and-publications/2024/10/ALS-GoldSpot-Discoveries-renamed-as-Geoanalytics-data-analysis-and-consulting-services)) | Need the client's own data; priced for funded juniors |
| **Farmonaut**, the direct competitor, which sells satellite gold targeting in Zimbabwe ([case study](https://farmonaut.com/mining/satellite-gold-prospectivity-mapping-in-zimbabwe)) | Already selling commercially | Closed method; self-reported results; no published validation |
| Free Google Earth Engine alteration scripts ([ISPRS 2025](https://isprs-archives.copernicus.org/articles/XLVIII-M-10-2025/191/2026/)) | Free | Need GIS skills; no national model, no validation |

**zim-prospect's edge:** 11 commodities nationally, validated scores, open data, offline and reproducible. **It lacks:** greenstone geology, magnetics, geochemistry, drill data, a title layer and a field track record.

## 4. Data access

- **ZGS geology.**
  - A 2024 fee-review draft lists a *vectorised geological map of Zimbabwe* at 100.00, 1:100,000 sheets at 15.00, and aeromagnetic maps at 20–250. The currency is unstated and the figures may not be final ([Scribd](https://www.scribd.com/document/721166271/MINING-FEES-REVIEW-DRAFT)).
  - BGS reports ZGS lacked digital vector maps, with a digital 1:1M map in preparation ([NORA](https://nora.nerc.ac.uk/id/eprint/541846/)).
  - **How:** buy from ZGS in Harare and confirm licence terms. Probably not redistributable, so load it via the U4 plug-in and never commit it.
- **Aeromagnetics. Check this first.** Natural Resources Canada lists two 1980s Zimbabwe surveys flown under the Canadian CIDA aid programme, released under the **Open Government Licence – Canada**:
  - [Zimbabwe (II) area 1](https://open.canada.ca/data/dataset/7a04ac9c-2174-53dc-b9f6-fe631bf5069e): 1988–89, 1 km lines, 155,606 line-km, roughly 40% of the country;
  - [Central Zimbabwe area 4](https://open.canada.ca/data/dataset/100513ae-9fce-5738-b1d6-9b752b2664c4): 250 m lines, magnetics plus electromagnetics (EM).

  The download links now redirect to geophysical-data.canada.ca, and I did not confirm the grids download. If they do, the README's "no open magnetics" is wrong, and this is the cheapest accuracy gain available.

## 5. Focus and product changes

**Segments:** consultants doing claim due diligence first, then small-scale gold miners organised in syndicates.

**Minerals:**

| Status | Minerals | Why |
|---|---|---|
| Focus | Gold | Largest market; the AUC can improve with greenstone geology and magnetics |
| Focus | Chromite | AUC 0.86. Small miners are moving from exhausted alluvial deposits to hard rock ([SMM](https://news.metal.com/newscontent/104081372-smm-chromium-flash-alluvial-exhaustion-forces-zimbabwes-chrome-miners-toward-costlier-hard-rock-ore)), which raises the value of targeting. |
| Set aside | Coal | Needs a special grant; the model rediscovers known basins |
| Set aside | Lithium | Export regime and strategic status; weak AUC |
| Set aside | Diamonds | Only 5 known deposits |

**Top five product changes:**

1. **Title and exclusion overlay.**
   - Mask parks, riverbeds and towns.
   - Import claim and EPO polygons.
   - Read the e-cadastre once it opens.
   - Flag "title unknown" on every target.
2. **Claim due-diligence report.** A claim polygon in, a one-page PDF out:
   - commodity scores and their percentiles within the belt;
   - nearest workings;
   - Sentinel-2 alteration patches;
   - caveats.

   This is the paid product.
3. **Field-sampling export.**
   - A 25–50 m sample grid with IDs, as GPX, KML or GeoPackage.
   - Upload assay or XRF results so they become new occurrences.
4. **Data upgrades.** Test the NRCan magnetics, buy the ZGS vector geology, then re-validate gold.
5. **Offline mobile maps and access control.**
   - Export MBTiles for QField or OsmAnd.
   - Put greenfield targets behind a login.

## Sources

- [AllAfrica, ASM gold share (Feb 2026)](https://allafrica.com/stories/202602230368.html)
- [Herald, a million ASM lives](https://www.heraldonline.co.zw/mapping-a-million-lives-beneath-the-surface/)
- [UNEP, planetGOLD Zimbabwe](https://www.unep.org/gef/news-and-stories/technical-highlight/planetgold-zimbabwe-takes-aim-mercury-use-small-scale-gold)
- [SMM, regularisation deadline](https://news.metal.com/newscontent/104103697-smm-chromium-flash-zimbabwes-artisanal-mining-deadline-extension-comes-as-chrome-features-in-fresh-smuggling-count)
- [SMM, chrome hard-rock shift](https://news.metal.com/newscontent/104081372-smm-chromium-flash-alluvial-exhaustion-forces-zimbabwes-chrome-miners-toward-costlier-hard-rock-ore)
- [Mines and Minerals Act (FAOLEX)](https://faolex.fao.org/docs/pdf/zim060737original.pdf)
- [Herald, prospecting rules](https://www.heraldonline.co.zw/prospecting-for-minerals/)
- [Bulawayo24, EPOs](https://bulawayo24.com/news/national/252651)
- [AllAfrica, Bill gazetted](https://allafrica.com/stories/202506260024.html)
- [MMM Law, Bill overview](https://www.mmmlawfirm.co.zw/overview-of-the-provisions-of-the-mines-and-minerals-bill-2025/)
- [FinGaz, e-cadastre](https://fingaz.co.zw/2026/06/21/government-targets-year-end-for-e-cadastre-launch/)
- [FinGaz, airborne survey](https://fingaz.co.zw/2026/06/25/government-to-map-mineral-reserves/)
- [Miningmx, parks ban](https://www.miningmx.com/news/energy/43610-zimbabwe-bans-mining-in-national-parks-after-hwange-coal-drilling-uproar/)
- [Mining.com, 2027 concentrate ban](https://www.mining.com/web/zimbabwe-to-ban-export-of-lithium-concentrates-from-2027/)
- [Al Jazeera, Feb 2026 export suspension](https://www.aljazeera.com/news/2026/2/25/zimbabwe-imposes-ban-on-exports-of-all-raw-minerals-and-lithium-concentrate)
- [Engineering News, lithium quotas](https://www.engineeringnews.co.za/article/zimbabwe-to-introduce-lithium-export-quotas-sets-conditions-for-resumption-of-shipments-2026-04-08)
- [Mining Index, Mberengwa rush](https://www.miningindex.co.zw/?p=24026)
- [PRIO, lithium tensions](https://www.prio.org/2023/03/renewable-energy-renewable-conflicts-how-a-lithium-rush-in-zimbabwe-is-raising-tensions/)
- [Mining Technology, Kavango](https://www.mining-technology.com/news/kavango-option-acquire-100-nara-gold-project-zimbabwe/)
- [Equity Axis, disputed claims](https://equityaxis.net/post/18848/2026/3/a-disputed-claim-in-a-country-full-of-them-zimbabwe-s-gold-rush-and-risks-investors-are-taking)
- [Skillings, KoBold](https://skillings.net/the-frontier-blueprint-how-kobold-metals-uses-ai-to-locate-deep-copper-in-zambia/)
- [VRIFY pricing](https://vrify.com/pricing)
- [ALS Geoanalytics](https://www.alsglobal.com/en/News-and-publications/2024/10/ALS-GoldSpot-Discoveries-renamed-as-Geoanalytics-data-analysis-and-consulting-services)
- [Farmonaut Zimbabwe case study](https://farmonaut.com/mining/satellite-gold-prospectivity-mapping-in-zimbabwe)
- [ISPRS 2025, Sentinel-2 in Google Earth Engine](https://isprs-archives.copernicus.org/articles/XLVIII-M-10-2025/191/2026/)
- [Scribd, fee-review draft](https://www.scribd.com/document/721166271/MINING-FEES-REVIEW-DRAFT)
- [BGS/NORA report](https://nora.nerc.ac.uk/id/eprint/541846/)
- [NRCan, Zimbabwe (II) area 1](https://open.canada.ca/data/dataset/7a04ac9c-2174-53dc-b9f6-fe631bf5069e)
- [NRCan, Central Zimbabwe area 4](https://open.canada.ca/data/dataset/100513ae-9fce-5738-b1d6-9b752b2664c4)
