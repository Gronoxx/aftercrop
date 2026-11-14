# Licenses of data and software

To be rechecked in the first hour of 14 November 2026 (plan, section 9.4). This is a technical reading, not legal advice.

The NASA Space Apps Terms ask for material that is free and unrestricted or, at least, open in the OSI sense, with its license documented. One line per material: license, where it was read, and the attribution text used.

## Project code

| Item | License | Note |
|---|---|---|
| Aftercrop code in this repository | Apache-2.0 | Full text in `LICENSE`, "Copyright 2026 The Aftercrop team" (SA-D68). The same license as the crop engine. No `NOTICE` file: Apache-2.0 asks for one only when the work already carries it, and third-party notices are kept below and next to the vendored files |

## Data

| Material | License read | Where | Attribution used |
|---|---|---|---|
| HLS v2.0 and HLS-VI v2.0 (HLSL30, HLSS30) | Open, no restriction | [Earthdata catalogue](https://www.earthdata.nasa.gov/data/catalog/lpcloud-hlss30-2.0) | "HLS and HLS-VI v2.0, NASA LP DAAC." Sentinel-2 part with the [Copernicus notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice): "Contains modified Copernicus Sentinel data" |
| NASA POWER daily (including `IMERG_PRECTOT`) | NASA data, cite. No explicit POWER license found | [NASA data use guidance](https://www.earthdata.nasa.gov/engage/open-data-services-software-policies/data-use-guidance) | "NASA POWER Project, daily point data." |
| NASADEM_HGT v001 | NASA data, no restriction, cite | [doi:10.5067/MEaSUREs/NASADEM/NASADEM_HGT.001](https://doi.org/10.5067/MEaSUREs/NASADEM/NASADEM_HGT.001) | "NASADEM_HGT v001, NASA JPL, LP DAAC." |
| SoilGrids 2.0 | CC BY 4.0 | [ISRIC](https://isric.org/explore/soilgrids) | "SoilGrids 2.0, ISRIC, CC BY 4.0." |
| Tábua de Risco do Zarc | CC BY | [dados.agricultura.gov.br](https://dados.agricultura.gov.br/dataset/tabua-de-risco-zoneamento-agricola-de-risco-climatico) | "Tábua de Risco do Zarc, MAPA, CC BY." No endorsement implied |
| GloREDa 1.2 | CC BY 4.0 (read in the record's `license.id`), Zenodo copy only | [Zenodo record 8036998](https://zenodo.org/records/8036998) | "Panagos et al. 2023, Data in Brief 50, 109482. Zenodo doi:10.5281/zenodo.8036998, CC BY 4.0." ESDAC copy not redistributed |
| IBGE municipal meshes | Used only to check trial coordinates, not shipped | [IBGE](https://www.ibge.gov.br/acesso-informacao/institucional/termo-de-uso.html) | Not shipped |
| Embrapa Soja Documentos 398, 459 and 476 | Conflict: "Todos os direitos reservados" in the PDF, CC BY-NC-ND 4.0 on a repository page | [Infoteca](https://www.infoteca.cnptia.embrapa.br/infoteca/handle/doc/1086382?mode=full) | Cited with document, page and table. Values are not shipped: the site gets 1 sc/ha steps only, until rights are clear (plan, section 9) |
| CONAB, CEPEA, Deral, PAM | CC BY-ND 3.0, CC BY-NC 4.0, none found, not confirmed | plan, section 9.3 | Link only. Nothing embedded |

## Software

| Package | Version | License | Source of the license |
|---|---|---|---|
| AquaCrop-OSPy (`aquacrop`) | 3.1.0 | Apache-2.0 | LICENSE file of the repository. The package metadata says MIT, and the LICENSE file prevails |
| numpy | 2.5.4 | BSD-3-Clause (bundled parts: 0BSD, MIT, Zlib, CC0-1.0) | installed package metadata |
| pandas | 3.1.1 | BSD-3-Clause | installed package metadata |
| PyYAML | 6.0.3 | MIT | installed package metadata |
| requests | 2.34.2 | Apache-2.0 | installed package metadata |
| tifffile | 2026.11.8 | BSD-3-Clause | installed package metadata |
| pytest (tests only) | 9.2.0 | MIT | installed package metadata |
| rasterio | 1.5.1 | BSD-3-Clause | installed package metadata |
| Alpine.js (`site/vendor/alpine-3.17.5.min.js`, served with the page) | 3.17.5 | MIT (bundled @vue/reactivity and @vue/shared 3.5.41, MIT) | `site/vendor/LICENSE-alpinejs.txt` and the notices at the end of the file, checked against `site/vendor/VENDOR.json` |

Packages added later get a line here when they enter `requirements.lock` or `site/vendor/`.
