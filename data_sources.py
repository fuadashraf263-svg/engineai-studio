#!/usr/bin/env python3
"""data_sources.py - the catalogue of places EngineAI can get real data.

WHAT AN ADAPTER IS HERE. No framework, no SDK per vendor. Each source is one
small function that calls a REST endpoint and maps its JSON into the single
`Dataset` shape data_fetch.py already uses: title, identifier, files, licence,
DOI, publisher. Everything downstream - checksum verification, licence gating,
quarantine, authenticity assessment - is shared, so adding a source is writing
a parser, not writing a pipeline.

That is deliberate. A hundred vendor SDKs would be a hundred dependency
upgrades and a hundred ways to fail; a hundred parsers against a common shape
is one pipeline with a hundred front doors.

HOW A SOURCE IS VERIFIED, in the order the checks actually run:

  1  transport      HTTPS only, and the certificate is checked
  2  provenance     a DOI that resolves through DataCite or Crossref means the
                    dataset is registered to a real depositor. This is the
                    strongest single signal and the reason DOI-issuing
                    repositories are ranked TIER 1 below.
  3  integrity      compare bytes against the checksum the registry published.
                    No published checksum is recorded as a gap, not ignored.
  4  licence        determined from the record, or the file is quarantined
  5  authenticity   data_authenticity.assess - is this a measurement, a
                    computation, or something with fabrication indicators
  6  physics        values checked against hard physical limits

Steps 1-4 establish where a file came from. Steps 5-6 ask whether the numbers
inside it are a record of anything. Those are different questions and a source
passing the first four says nothing about the last two.

TIERS
  1  DOI-issuing repositories with checksums and machine-readable licences
  2  authoritative institutional databases, stable APIs, clear terms
  3  useful aggregators, weaker provenance - treat as leads, verify separately
  4  needs a key and/or has redistribution limits - read the terms before use
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

__all__ = ["SOURCES", "by_discipline", "by_tier", "needing_key", "implemented",
           "Source", "summary"]


@dataclass
class Source:
    key: str
    name: str
    discipline: str
    tier: int
    base: str
    auth: str                    # "none" | "key" | "oauth" | "account"
    provides: str
    licence_posture: str
    doi: bool = False
    checksums: bool = False
    implemented: bool = False    # is there a parser in data_fetch.py yet
    env: str = ""
    notes: str = ""


def S(*a, **k) -> Source:
    return Source(*a, **k)


SOURCES: list[Source] = [
    # ---------------- TIER 1 - DOI repositories, the backbone ----------------
    S("zenodo", "Zenodo", "general", 1, "https://zenodo.org/api", "none",
      "any research dataset, CERN-operated", "per-record, machine readable",
      doi=True, checksums=True, implemented=True),
    S("datacite", "DataCite", "general", 1, "https://api.datacite.org", "none",
      "the DOI registry itself - authoritative provenance for 60M+ records",
      "per-record", doi=True, implemented=True),
    S("crossref", "Crossref", "literature", 1, "https://api.crossref.org", "none",
      "160M+ scholarly works, funders, citations", "metadata is CC0", doi=True),
    S("dryad", "Dryad", "general", 1, "https://datadryad.org/api/v2", "none",
      "curated research data, strong curation", "CC0 by policy", doi=True,
      checksums=True),
    S("figshare", "Figshare", "general", 1, "https://api.figshare.com/v2", "key",
      "datasets, figures, theses", "per-record", doi=True, checksums=True,
      env="FIGSHARE_TOKEN"),
    S("osf", "Open Science Framework", "general", 1, "https://api.osf.io/v2",
      "key", "preregistrations, materials, data", "per-project", doi=True,
      env="OSF_TOKEN"),
    S("openaire", "OpenAIRE", "general", 1, "https://api.openaire.eu", "none",
      "EU-funded research outputs, aggregated", "per-record", doi=True),
    S("mendeley_data", "Mendeley Data", "general", 1,
      "https://api.data.mendeley.com", "key", "datasets linked to publications",
      "per-record", doi=True, env="MENDELEY_TOKEN"),

    # ---------------- materials and chemistry ----------------
    S("materials_project", "Materials Project", "materials", 2,
      "https://api.materialsproject.org", "key",
      "150k+ inorganic materials: formation energy, band gap, elastic tensor, "
      "phonons", "CC-BY-4.0", env="MP_API_KEY"),
    S("nomad", "NOMAD", "materials", 2, "https://nomad-lab.eu/prod/v1/api/v1",
      "none", "20M+ DFT calculations, raw input and output files", "CC-BY-4.0",
      doi=True),
    S("oqmd", "Open Quantum Materials DB", "materials", 2,
      "https://oqmd.org/oqmdapi", "none",
      "1M+ DFT formation energies and stability", "permissive, cite the paper"),
    S("aflow", "AFLOW", "materials", 2, "https://aflow.org/API", "none",
      "3M+ compounds, elastic and thermal properties", "cite the paper"),
    S("jarvis", "JARVIS-DFT (NIST)", "materials", 2,
      "https://jarvis.nist.gov/jarvisdft", "none",
      "NIST DFT: 2D materials, elastic, dielectric, solar", "US public domain"),
    S("cod", "Crystallography Open Database", "materials", 2,
      "https://www.crystallography.net/cod", "none",
      "500k+ crystal structures, CIF files", "CC0"),
    S("mpds", "Materials Platform for Data Science", "materials", 4,
      "https://api.mpds.io", "key", "curated phase diagrams and properties",
      "commercial licence", env="MPDS_KEY"),
    S("matweb", "MatWeb", "materials", 4, "https://www.matweb.com", "account",
      "150k+ commercial material datasheets", "NOT redistributable",
      notes="scraping prohibited - use only via their licensed API"),
    S("pubchem", "PubChem", "chemistry", 2,
      "https://pubchem.ncbi.nlm.nih.gov/rest/pug", "none",
      "119M+ compounds: properties, safety, spectra", "US public domain",
      env="NCBI_API_KEY", notes="key only raises the rate limit"),
    S("nist_webbook", "NIST Chemistry WebBook", "chemistry", 2,
      "https://webbook.nist.gov", "none",
      "thermochemistry, phase change, IR/MS spectra", "US Govt, cite NIST"),
    S("nist_srd", "NIST Standard Reference Data", "reference", 1,
      "https://data.nist.gov/rmm/records", "none",
      "certified reference data, including AM-Bench", "US public domain",
      doi=True),
    S("codata", "CODATA Fundamental Constants", "reference", 1,
      "https://physics.nist.gov/cuu/Constants", "none",
      "SI constants with uncertainties", "public", implemented=True,
      notes="already vendored in refdata.py"),
    S("chemspider", "ChemSpider", "chemistry", 4,
      "https://api.rsc.org/compounds/v1", "key", "115M+ structures",
      "RSC terms", env="CHEMSPIDER_KEY"),
    S("rcsb_pdb", "RCSB Protein Data Bank", "biochemistry", 2,
      "https://data.rcsb.org/rest/v1", "none", "230k+ 3D macromolecules", "CC0"),

    # ---------------- mechanical, structural, civil ----------------
    S("nist_ambench", "NIST AM-Bench", "manufacturing", 1,
      "https://www.nist.gov/ambench", "none",
      "additive manufacturing benchmark measurements - THE reference for "
      "validating AM simulation", "US public domain", doi=True),
    S("peer_nga", "PEER NGA Strong Motion", "structural", 2,
      "https://ngawest2.berkeley.edu", "account",
      "earthquake ground motion records", "registration required",
      notes="essential for seismic work; account is free"),
    S("strongmotion_center", "Center for Engineering Strong Motion Data",
      "structural", 2, "https://www.strongmotioncenter.org", "none",
      "instrumented building and ground records", "US public domain"),
    S("usgs_earthquake", "USGS Earthquake Hazards", "geotechnical", 2,
      "https://earthquake.usgs.gov/fdsnws/event/1", "none",
      "global seismicity, hazard curves, ShakeMap", "US public domain"),
    S("nasa_mmpds_ref", "MMPDS (Metallic Materials Properties)", "aerospace", 4,
      "https://www.mmpds.org", "account",
      "design allowables for aerospace metals", "PAID - not redistributable",
      notes="the industry standard for allowables; licence required"),
    S("matbase", "MatBase", "materials", 3, "https://www.matbase.com", "none",
      "engineering material properties", "check terms"),

    # ---------------- fluids, thermal, energy ----------------
    S("nist_refprop_ref", "NIST REFPROP", "thermodynamics", 4,
      "https://www.nist.gov/srd/refprop", "account",
      "reference fluid thermodynamic and transport properties",
      "PAID licence", notes="CoolProp is the free alternative and is wired"),
    S("coolprop", "CoolProp", "thermodynamics", 2,
      "http://www.coolprop.org", "none",
      "122 fluids, IAPWS-grade equations of state", "MIT",
      notes="a library, not a service - pip install CoolProp"),
    S("nrel_nsrdb", "NREL Solar Radiation DB", "energy", 2,
      "https://developer.nrel.gov/api/nsrdb", "key",
      "half-hourly solar irradiance, 4km grid, 1998-present", "US public domain",
      env="NREL_API_KEY"),
    S("nrel_wind", "NREL WIND Toolkit", "energy", 2,
      "https://developer.nrel.gov/api/wind-toolkit", "key",
      "wind resource, 5-minute, 2km", "US public domain", env="NREL_API_KEY"),
    S("eia", "US Energy Information Administration", "energy", 2,
      "https://api.eia.gov/v2", "key",
      "generation, consumption, prices, emissions", "US public domain",
      env="EIA_API_KEY"),
    S("entsoe", "ENTSO-E Transparency", "energy", 2,
      "https://web-api.tp.entsoe.eu/api", "key",
      "European grid: load, generation, cross-border flow", "free with account",
      env="ENTSOE_TOKEN"),
    S("openei", "OpenEI", "energy", 3, "https://openei.org/services/api", "key",
      "utility rates, building loads", "mixed", env="OPENEI_KEY"),

    # ---------------- earth, environment, climate ----------------
    S("noaa_cdo", "NOAA Climate Data Online", "environment", 2,
      "https://www.ncdc.noaa.gov/cdo-web/api/v2", "key",
      "global weather station records, hourly to annual", "US public domain",
      env="NOAA_TOKEN"),
    S("copernicus_cds", "Copernicus Climate Data Store", "environment", 2,
      "https://cds.climate.copernicus.eu/api/v2", "key",
      "ERA5 reanalysis - the standard climate dataset", "Copernicus licence",
      env="CDSAPI_KEY"),
    S("openaq", "OpenAQ", "environment", 2, "https://api.openaq.org/v3", "key",
      "global air quality, station level, real time", "CC-BY-4.0",
      env="OPENAQ_API_KEY"),
    S("epa_aqs", "US EPA Air Quality System", "environment", 2,
      "https://aqs.epa.gov/data/api", "key", "regulatory-grade US air quality",
      "US public domain", env="EPA_AQS_KEY"),
    S("usgs_water", "USGS Water Services", "civil", 2,
      "https://waterservices.usgs.gov/nwis", "none",
      "streamflow, groundwater, water quality", "US public domain"),
    S("nasa_earthdata", "NASA Earthdata / CMR", "earth", 2,
      "https://cmr.earthdata.nasa.gov/search", "key",
      "satellite earth observation, all NASA missions", "US public domain",
      env="NASA_EARTHDATA_TOKEN"),
    S("nasa_open", "NASA Open Data", "aerospace", 2,
      "https://api.nasa.gov", "key", "planetary, techport, imagery",
      "US public domain", env="NASA_API_KEY"),
    S("esa_open", "ESA Open Data", "aerospace", 3,
      "https://open.esa.int", "none", "European space mission data", "ESA terms"),

    # ---------------- electrical, electronics ----------------
    S("octopart", "Octopart", "electronics", 4, "https://octopart.com/api/v4",
      "key", "electronic component parametrics, stock, price",
      "commercial terms", env="OCTOPART_KEY"),
    S("digikey", "DigiKey", "electronics", 4,
      "https://api.digikey.com", "oauth", "component parameters and datasheets",
      "commercial terms", env="DIGIKEY_CLIENT_ID"),
    S("mouser", "Mouser", "electronics", 4, "https://api.mouser.com", "key",
      "component search and datasheets", "commercial", env="MOUSER_KEY"),
    S("nrel_bms", "Battery Failure Databank (NREL)", "electrical", 2,
      "https://www.nrel.gov/transportation/battery-failure.html", "none",
      "cell failure and thermal runaway measurements", "US public domain"),
    S("nasa_battery", "NASA Prognostics Data Repository", "reliability", 1,
      "https://www.nasa.gov/intelligent-systems-division", "none",
      "run-to-failure: batteries, bearings, turbofan (C-MAPSS)",
      "US public domain", notes="the standard prognostics benchmark set"),

    # ---------------- machine learning benchmarks ----------------
    S("uci", "UCI ML Repository", "benchmarks", 2,
      "https://archive.ics.uci.edu", "none",
      "670+ classic datasets, documented units", "per-dataset",
      implemented=True),
    S("openml", "OpenML", "benchmarks", 2, "https://www.openml.org/api/v1",
      "none", "5000+ datasets with rich metadata and runs", "mostly CC-BY",
      implemented=True),
    S("kaggle", "Kaggle", "benchmarks", 3, "https://www.kaggle.com/api/v1",
      "key", "300k+ community datasets", "per-dataset, varies wildly",
      env="KAGGLE_KEY", notes="provenance is often weak - verify before using"),
    S("huggingface", "Hugging Face Datasets", "benchmarks", 3,
      "https://huggingface.co/api/datasets", "key",
      "200k+ datasets, streaming supported", "per-dataset",
      env="HF_TOKEN", notes="streaming matters for data larger than RAM"),
    S("papers_with_code", "Papers with Code", "benchmarks", 3,
      "https://paperswithcode.com/api/v1", "none",
      "benchmark leaderboards linked to code", "CC-BY-SA"),

    # ---------------- government and statistics portals ----------------
    S("ckan", "CKAN portals", "government", 2,
      "https://catalog.data.gov/api/3", "none",
      "data.gov, data.gov.uk, open.canada.ca and hundreds more", "per-dataset",
      implemented=True),
    S("eurostat", "Eurostat", "government", 2,
      "https://ec.europa.eu/eurostat/api/dissemination", "none",
      "European statistics: energy, industry, transport", "reuse permitted"),
    S("worldbank", "World Bank Open Data", "government", 2,
      "https://api.worldbank.org/v2", "none",
      "development indicators, infrastructure, energy", "CC-BY-4.0"),
    S("oecd", "OECD Data", "government", 2,
      "https://sdmx.oecd.org/public/rest", "none",
      "economic and environmental indicators", "OECD terms"),
    S("bgs", "British Geological Survey", "geotechnical", 2,
      "https://www.bgs.ac.uk/data", "none", "boreholes, geology, ground data",
      "OGL"),

    # ---------------- literature and novelty ----------------
    S("arxiv", "arXiv", "literature", 2, "http://export.arxiv.org/api", "none",
      "2.4M+ preprints: physics, engineering, CS", "per-paper, metadata open"),
    S("semantic_scholar", "Semantic Scholar", "literature", 2,
      "https://api.semanticscholar.org/graph/v1", "key",
      "200M+ papers with citation graph - THE novelty check",
      "metadata open", env="S2_API_KEY",
      notes="needed to answer 'has anyone already found this?'"),
    S("openalex", "OpenAlex", "literature", 1,
      "https://api.openalex.org", "none",
      "250M+ works, fully open successor to MS Academic", "CC0",
      notes="no key, no rate limit with a mailto - best first choice"),
    S("pubmed", "PubMed / NCBI E-utilities", "literature", 2,
      "https://eutils.ncbi.nlm.nih.gov/entrez/eutils", "key",
      "36M+ biomedical citations", "US public domain", env="NCBI_API_KEY"),
    S("core", "CORE", "literature", 3, "https://api.core.ac.uk/v3", "key",
      "290M+ open access full texts", "per-paper", env="CORE_KEY"),
    S("unpaywall", "Unpaywall", "literature", 2,
      "https://api.unpaywall.org/v2", "none",
      "finds the legal free copy of a paywalled paper", "CC0",
      notes="use this instead of a pirate mirror, always"),

    # ---------------- standards - metadata only ----------------
    S("iso_browse", "ISO Online Browsing Platform", "standards", 4,
      "https://www.iso.org/obp", "none",
      "standard NUMBERS, titles, scope, status - NOT the text",
      "text is copyrighted and must be purchased",
      notes="EngineAI implements formulae, never reproduces clause text"),
    S("nist_standards", "NIST Engineering Statistics Handbook", "standards", 2,
      "https://www.itl.nist.gov/div898/handbook", "none",
      "statistical methods with worked examples", "US public domain"),

    # ================= PATENTS - the other half of novelty ==================
    # Checking papers alone answers "has anyone PUBLISHED this". A great deal
    # of engineering is patented and never published, so a novelty claim that
    # skips patents is half a check.
    S("epo_ops", "EPO Open Patent Services", "patents", 2,
      "https://ops.epo.org/3.2/rest-services", "oauth",
      "150M+ patent documents worldwide, full text and families",
      "free tier 4GB/month", env="EPO_OPS_KEY"),
    S("patentsview", "USPTO PatentsView", "patents", 1,
      "https://search.patentsview.org/api/v1", "key",
      "US patents with inventors, assignees, citations, CPC classes",
      "US public domain", env="PATENTSVIEW_KEY"),
    S("uspto_ppubs", "USPTO Patent Public Search", "patents", 2,
      "https://developer.uspto.gov/api-catalog", "none",
      "US patent and application full text", "US public domain"),
    S("lens_org", "Lens.org", "patents", 3, "https://api.lens.org", "key",
      "patents linked to scholarly work - the best paper-to-patent bridge",
      "free for non-commercial", env="LENS_TOKEN"),
    S("google_patents_bq", "Google Patents (BigQuery)", "patents", 3,
      "https://console.cloud.google.com/bigquery", "oauth",
      "full-text patents worldwide, SQL queryable", "Google Cloud terms",
      env="GOOGLE_APPLICATION_CREDENTIALS"),

    # ================= CAD, PLM, geometry ==================
    S("onshape", "Onshape", "cad", 2, "https://cad.onshape.com/api/v10", "key",
      "full parametric CAD in the cloud: create, modify, export STEP/STL",
      "per-account", env="ONSHAPE_ACCESS_KEY",
      notes="the only major CAD with a first-class REST API"),
    S("autodesk_aps", "Autodesk Platform Services", "cad", 2,
      "https://developer.api.autodesk.com", "oauth",
      "model derivative, viewer, BIM 360, Fusion data", "per-account",
      env="APS_CLIENT_ID"),
    S("traceparts", "TraceParts", "cad", 3, "https://www.traceparts.com/api",
      "key", "100M+ supplier CAD models: fasteners, bearings, actuators",
      "per-supplier terms", env="TRACEPARTS_KEY"),
    S("grabcad", "GrabCAD", "cad", 3, "https://grabcad.com", "account",
      "community CAD library", "per-model licence"),
    S("opencascade_ref", "OpenCascade / OCCT", "cad", 2,
      "https://dev.opencascade.org", "none",
      "the geometry kernel EngineAI already uses via cadquery", "LGPL-2.1",
      implemented=True),

    # ================= manufacturing, DFM, quoting ==================
    S("xometry", "Xometry", "manufacturing", 4,
      "https://xometry.com/api", "key",
      "instant quoting and DFM feedback: CNC, sheet, injection, AM",
      "commercial", env="XOMETRY_KEY",
      notes="answers 'what will this part cost to make' with a real quote"),
    S("protolabs", "Protolabs", "manufacturing", 4,
      "https://www.protolabs.com", "account",
      "instant quote and manufacturability analysis", "commercial"),
    S("hubs", "Hubs (Protolabs Network)", "manufacturing", 4,
      "https://www.hubs.com", "account", "distributed manufacturing quotes",
      "commercial"),
    S("mcmaster", "McMaster-Carr", "supply", 4, "https://www.mcmaster.com", "account",
      "700k+ standard parts with CAD and specifications",
      "account required, no scraping"),
    S("misumi", "MISUMI", "supply", 4, "https://us.misumi-ec.com", "account",
      "configurable mechanical components with CAD", "commercial"),

    # ================= electronics supply ==================
    S("nexar", "Nexar (Altium)", "electronics", 4, "https://api.nexar.com",
      "oauth", "unified component search, pricing, lifecycle, alternates",
      "commercial", env="NEXAR_CLIENT_ID"),
    S("snapeda", "SnapEDA", "electronics", 3, "https://www.snapeda.com/api",
      "key", "symbols, footprints and 3D models for components",
      "per-part", env="SNAPEDA_KEY"),
    S("element14", "element14 / Farnell", "electronics", 4,
      "https://api.element14.com", "key", "component search and stock",
      "commercial", env="ELEMENT14_KEY"),

    # ================= codes, regulations, compliance ==================
    S("ecfr", "eCFR (US Code of Federal Regulations)", "regulatory", 1,
      "https://www.ecfr.gov/api/versioner/v1", "none",
      "full text of US federal regulations, versioned", "US public domain"),
    S("osha", "OSHA Enforcement and Standards", "safety", 2,
      "https://enforcedata.dol.gov/views/data_summary.php", "none",
      "workplace safety standards and inspection outcomes", "US public domain"),
    S("echa", "ECHA (EU Chemicals Agency)", "regulatory", 2,
      "https://echa.europa.eu", "none",
      "REACH registrations, SVHC candidate list, restrictions", "EU reuse"),
    S("gestis", "GESTIS Substance Database", "safety", 2,
      "https://gestis.dguv.de", "none",
      "hazardous substance handling and exposure limits", "free use"),
    S("epa_envirofacts", "EPA Envirofacts", "regulatory", 2,
      "https://data.epa.gov/efservice", "none",
      "US emissions, discharge, hazardous waste by facility", "US public domain"),

    # ================= incidents and failures - learning from what broke =====
    S("ntsb", "NTSB Aviation and Surface Accidents", "reliability", 1,
      "https://data.ntsb.gov/carol-main-public/api", "none",
      "full accident investigations with probable cause", "US public domain",
      notes="the best public record of how engineered systems actually fail"),
    S("csb", "US Chemical Safety Board", "safety", 2,
      "https://www.csb.gov", "none",
      "process safety incident investigations", "US public domain"),
    S("faa_asrs", "NASA Aviation Safety Reporting System", "reliability", 2,
      "https://asrs.arc.nasa.gov", "none",
      "1.7M+ confidential near-miss reports", "US public domain"),
    S("nasa_lessons", "NASA Lessons Learned Information System", "reliability", 2,
      "https://llis.nasa.gov", "none",
      "engineering lessons from missions and failures", "US public domain"),
    S("fda_maude", "FDA MAUDE", "biomedical", 2,
      "https://api.fda.gov/device", "none",
      "medical device adverse events and failures", "US public domain"),
    S("cpsc", "CPSC Consumer Product Safety", "safety", 2,
      "https://www.saferproducts.gov/RestWebServices", "none",
      "consumer product incident reports and recalls", "US public domain"),

    # ================= geospatial, terrain, site data ==================
    S("overpass", "OpenStreetMap Overpass API", "geospatial", 2,
      "https://overpass-api.de/api", "none",
      "buildings, roads, infrastructure geometry worldwide", "ODbL"),
    S("usgs_3dep", "USGS 3DEP Elevation", "geospatial", 2,
      "https://elevation.nationalmap.gov/arcgis/rest", "none",
      "1m to 30m digital elevation for the US", "US public domain"),
    S("opentopography", "OpenTopography", "geospatial", 2,
      "https://portal.opentopography.org/API", "key",
      "global lidar and DEM, SRTM, Copernicus DEM", "per-dataset",
      env="OPENTOPO_KEY"),
    S("soilgrids", "SoilGrids (ISRIC)", "geotechnical", 2,
      "https://rest.isric.org/soilgrids/v2.0", "none",
      "global soil properties at 250m: density, texture, organic carbon",
      "CC-BY-4.0"),
    S("nrcs_soil", "USDA NRCS Soil Data Access", "geotechnical", 2,
      "https://sdmdataaccess.sc.egov.usda.gov", "none",
      "detailed US soil survey, bearing and drainage", "US public domain"),

    # ================= design loads: wind, seismic, snow, thermal ==========
    S("asce7_hazard", "ASCE 7 Hazard Tool", "structural", 2,
      "https://ascehazardtool.org", "none",
      "site-specific wind, seismic, snow, ice, rain loads for the US",
      "free, cite ASCE", notes="the design loads themselves, by latitude and "
      "longitude - a genuine gap in EngineAI today"),
    S("open_meteo", "Open-Meteo", "environment", 1,
      "https://api.open-meteo.com/v1", "none",
      "weather forecast and 80-year reanalysis, no key, no limit", "CC-BY-4.0",
      notes="best keyless weather source; historical wind and temperature "
      "for load cases"),
    S("meteostat", "Meteostat", "environment", 2,
      "https://dev.meteostat.net/api", "key",
      "historical weather from 30k+ stations", "CC-BY-NC", env="METEOSTAT_KEY",
      notes="NON-COMMERCIAL licence - check before shipping"),

    # ================= nuclear, radiation, high energy ==================
    S("iaea_nds", "IAEA Nuclear Data Services", "nuclear", 1,
      "https://www-nds.iaea.org", "none",
      "evaluated nuclear reaction and decay data", "free use"),
    S("nndc_endf", "NNDC ENDF/B", "nuclear", 1,
      "https://www.nndc.bnl.gov/endf", "none",
      "cross sections for reactor and shielding calculations",
      "US public domain"),
    S("cern_opendata", "CERN Open Data", "physics", 1,
      "https://opendata.cern.ch/api", "none",
      "collider datasets and analysis software", "CC0", doi=True),

    # ================= optics, photonics, acoustics ==================
    S("refractiveindex", "refractiveindex.info", "optics", 2,
      "https://refractiveindex.info", "none",
      "refractive index and extinction for thousands of materials", "CC0"),
    S("hitran", "HITRAN", "optics", 2, "https://hitran.org/api", "key",
      "molecular spectroscopic line lists for radiative transfer",
      "free with registration", env="HITRAN_KEY"),

    # ================= transport, mobility, tracking ==================
    S("opensky", "OpenSky Network", "aerospace", 2,
      "https://opensky-network.org/api", "account",
      "live and historical aircraft state vectors", "free for research",
      env="OPENSKY_USER"),
    S("gtfs", "GTFS / Transitland", "transport", 2,
      "https://transit.land/api/v2", "key",
      "public transit schedules and geometry worldwide", "per-agency",
      env="TRANSITLAND_KEY"),

    # ================= space, orbital ==================
    S("jpl_horizons", "JPL Horizons", "aerospace", 1,
      "https://ssd-api.jpl.nasa.gov/horizons.api", "none",
      "solar system ephemerides to metre accuracy", "US public domain",
      notes="the authoritative source for orbital mechanics validation"),
    S("celestrak", "CelesTrak", "aerospace", 2, "https://celestrak.org/NORAD",
      "none", "TLEs for every tracked satellite", "free use"),
    S("spacetrack", "Space-Track.org", "aerospace", 2,
      "https://www.space-track.org", "account",
      "authoritative US catalogue of orbital objects", "free with account",
      env="SPACETRACK_USER"),

    # ================= sustainability, LCA, carbon ==================
    S("climatiq", "Climatiq", "sustainability", 2,
      "https://api.climatiq.io", "key",
      "emission factors for materials, energy, freight, computing",
      "free tier", env="CLIMATIQ_KEY",
      notes="turns the assumed CO2 factors in the concrete study into "
      "sourced ones"),
    S("ecoinvent", "ecoinvent", "sustainability", 4,
      "https://ecoquery.ecoinvent.org", "account",
      "the reference LCA inventory database", "PAID licence",
      env="ECOINVENT_USER"),
    S("openlca_nexus", "openLCA Nexus", "sustainability", 3,
      "https://nexus.openlca.org", "account",
      "free and paid LCA databases in one place", "mixed"),
    S("epa_greet", "EPA / ANL GREET", "sustainability", 2,
      "https://greet.anl.gov", "none",
      "well-to-wheel energy and emissions for fuels and vehicles",
      "US public domain"),

    # ================= biomedical and biomechanics ==================
    S("physionet", "PhysioNet", "biomedical", 1,
      "https://physionet.org/api", "account",
      "physiological signals: ECG, EEG, gait, ICU waveforms",
      "per-dataset, some restricted", doi=True),

    # ================= robotics and control ==================
    S("ros_index", "ROS Index", "robotics", 3, "https://index.ros.org", "none",
      "ROS package metadata and dependencies", "per-package"),

    # ================= metrology and units ==================
    S("bipm", "BIPM", "reference", 1, "https://www.bipm.org", "none",
      "the SI itself: definitions, brochure, key comparisons", "free use"),
    S("nist_wds", "NIST Physical Reference Data", "reference", 1,
      "https://physics.nist.gov/PhysRefData", "none",
      "atomic spectra, X-ray attenuation, ionisation", "US public domain"),

    # ================= language models and compute ==================
    # NOT NEEDED. Ollama does the whole job locally and free, and the routing
    # measurement that justified a language layer at all (5 of 10 phrasings
    # reaching a solver, against 10 of 10) was taken with the LOCAL 7B model.
    # These two are catalogued so the option is documented, tier 4 so nothing
    # reaches for them, and neither is on any default path.
    S("anthropic", "Anthropic API", "ai", 4, "https://api.anthropic.com/v1",
      "key", "hosted models - NOT REQUIRED, Ollama covers this locally",
      "PAID per token", env="ANTHROPIC_API_KEY",
      notes="billable. Nothing in EngineAI calls this unless you construct "
            "agent_loop.CloudBackend deliberately."),
    S("openai", "OpenAI API", "ai", 4, "https://api.openai.com/v1", "key",
      "hosted models - NOT REQUIRED, Ollama covers this locally",
      "PAID per token", env="OPENAI_API_KEY",
      notes="billable, and on no default path"),
    S("ollama", "Ollama (local)", "ai", 1, "http://127.0.0.1:11434", "none",
      "local models: qwen2.5 7B and 14B already pulled. Nothing leaves the "
      "machine and nothing is billed", "per-model",
      implemented=True, notes="wired in nl_router.py; this is the model layer"),
]


# Where to actually GET each credential. Kept beside the catalogue so the two
# cannot drift: a source that needs a key and has no signup link here is a
# dead end for the user, and the selftest fails on it.
SIGNUP = {
    # --- free, instant, no approval -------------------------------------
    "materials_project": "https://next-gen.materialsproject.org/api  (free, instant)",
    "nasa_open": "https://api.nasa.gov  (free, instant, email only)",
    "nrel_nsrdb": "https://developer.nrel.gov/signup  (free, instant - one key "
                  "covers NSRDB, WIND Toolkit and OpenEI)",
    "nrel_wind": "https://developer.nrel.gov/signup  (free, instant - same key "
                 "as NSRDB)",
    "pubmed": "https://account.ncbi.nlm.nih.gov  (free - the same NCBI_API_KEY "
              "serves PubChem and PubMed)",
    "eia": "https://www.eia.gov/opendata/register.php  (free, instant)",
    "openaq": "https://explore.openaq.org/register  (free, instant)",
    "ncbi": "https://account.ncbi.nlm.nih.gov  (free - Account Settings > API Key Management)",
    "semantic_scholar": "https://www.semanticscholar.org/product/api#api-key-form  (free, ~1 week)",
    "patentsview": "https://patentsview.org/apis/keyrequest  (free, instant)",
    "climatiq": "https://www.climatiq.io/pricing  (free tier, instant)",
    "opentopography": "https://portal.opentopography.org/myopentopo  (free, instant)",
    "hitran": "https://hitran.org/register  (free, instant)",
    "core": "https://core.ac.uk/services/api  (free, instant)",
    "meteostat": "https://rapidapi.com/meteostat/api/meteostat  (free tier - CC-BY-NC)",
    "figshare": "https://figshare.com/account/applications  (free)",
    "osf": "https://osf.io/settings/tokens  (free)",
    "mendeley_data": "https://dev.mendeley.com/myapps.html  (free)",
    "kaggle": "https://www.kaggle.com/settings  >  Create New Token  (free)",
    "huggingface": "https://huggingface.co/settings/tokens  (free)",
    "gtfs": "https://www.transit.land/documentation  (free tier, instant)",
    "snapeda": "https://www.snapeda.com/api  (free tier)",
    "lens_org": "https://www.lens.org/lens/user/subscriptions  (free non-commercial)",
    "openai": "https://platform.openai.com/api-keys  (paid per token)",
    "anthropic": "https://console.anthropic.com/settings/keys  (paid per token)",

    # --- free but needs approval or an application ----------------------
    "noaa_cdo": "https://www.ncdc.noaa.gov/cdo-web/token  (free, email token)",
    "copernicus_cds": "https://cds.climate.copernicus.eu/user/register  "
                      "(free; accept the ERA5 licence in your profile)",
    "nasa_earthdata": "https://urs.earthdata.nasa.gov/users/new  (free account)",
    "entsoe": "https://transparency.entsoe.eu  > register, then email "
              " asking for API access  (free, ~3 days)",
    "epa_aqs": "https://aqs.epa.gov/data/api/signup?email=YOUR_EMAIL  (free, instant)",
    "epo_ops": "https://developers.epo.org/  (free tier 4 GB/month, OAuth)",
    "opensky": "https://opensky-network.org/index.php?option=com_users&view=registration",
    "spacetrack": "https://www.space-track.org/auth/createAccount  (free, US export terms)",
    "physionet": "https://physionet.org/register/  (free; credentialed sets need "
                 "a CITI training certificate)",
    "peer_nga": "https://ngawest2.berkeley.edu/users/sign_up  (free account)",
    "openei": "https://openei.org/services/  (free, uses the NREL key)",

    # --- commercial: expect sales contact or a paid plan ----------------
    "onshape": "https://dev-portal.onshape.com/keys  (free for the free CAD tier)",
    "autodesk_aps": "https://aps.autodesk.com/myapps/create  (free tier available)",
    "nexar": "https://portal.nexar.com/sign-up  (free tier, then paid)",
    "octopart": "https://octopart.com/api/home  (now via Nexar)",
    "digikey": "https://developer.digikey.com/  (free, OAuth)",
    "mouser": "https://www.mouser.com/api-hub/  (free with account)",
    "element14": "https://partner.element14.com/  (free with account)",
    "traceparts": "https://www.traceparts.com/en/api  (contact sales)",
    "xometry": "https://www.xometry.com/api/  (contact sales)",
    "chemspider": "https://developer.rsc.org/  (free tier)",
    "mpds": "https://mpds.io/#pay  (paid)",
    "ecoinvent": "https://ecoinvent.org/offerings/  (paid licence)",
    "google_patents_bq": "https://console.cloud.google.com/  (Google Cloud billing)",

    # --- paid references, no API key: you buy the document --------------
    "nasa_mmpds_ref": "https://www.mmpds.org/  (PAID - the aerospace allowables "
                      "standard; no free tier exists)",
    "nist_refprop_ref": "https://www.nist.gov/srd/refprop  (PAID ~$325; CoolProp "
                        "is the free alternative and is already usable)",
    "matweb": "https://www.matweb.com/services/  (paid API; scraping prohibited)",
    "mcmaster": "https://www.mcmaster.com/  (account; ask them about API access)",
    "misumi": "https://us.misumi-ec.com/  (account)",
    "protolabs": "https://www.protolabs.com/  (account)",
    "hubs": "https://www.hubs.com/  (account)",
    "openlca_nexus": "https://nexus.openlca.org/  (free and paid databases)",
    "grabcad": "https://grabcad.com/  (free account)",
}


def signup_links(only_missing: bool = False) -> list:
    """Every credential the catalogue needs, with where to get it."""
    out = []
    for s in SOURCES:
        if s.auth == "none":
            continue
        if only_missing:
            try:
                import api_keys
                if api_keys.get(s.key):
                    continue
            except Exception:
                pass
        out.append({
            "key": s.key, "name": s.name, "discipline": s.discipline,
            "tier": s.tier, "env": s.env or "(account, no env var)",
            "unlocks": s.provides,
            "signup": SIGNUP.get(s.key, s.base),
            "licence": s.licence_posture,
        })
    return sorted(out, key=lambda r: (r["tier"], r["discipline"], r["key"]))


def by_discipline(d: str) -> list:
    return [s for s in SOURCES if s.discipline == d]


def by_tier(t: int) -> list:
    return [s for s in SOURCES if s.tier == t]


def needing_key() -> list:
    return [s for s in SOURCES if s.auth in ("key", "oauth", "account")]


def implemented() -> list:
    return [s for s in SOURCES if s.implemented]


def summary() -> dict:
    disciplines: dict = {}
    for s in SOURCES:
        disciplines.setdefault(s.discipline, 0)
        disciplines[s.discipline] += 1
    return {
        "total": len(SOURCES),
        "implemented": len(implemented()),
        "no_key_needed": len([s for s in SOURCES if s.auth == "none"]),
        "need_key": len(needing_key()),
        "doi_issuing": len([s for s in SOURCES if s.doi]),
        "with_checksums": len([s for s in SOURCES if s.checksums]),
        "disciplines": dict(sorted(disciplines.items(), key=lambda t: -t[1])),
        "env_vars": sorted({s.env for s in SOURCES if s.env}),
    }


def _selftest() -> int:
    ok = fail = 0

    def chk(c, label):
        nonlocal ok, fail
        if c:
            ok += 1
        else:
            fail += 1
            print(f"  FAIL {label}")

    s = summary()
    chk(s["total"] >= 55, f"{s['total']} sources catalogued")
    chk(s["no_key_needed"] >= 25, f"{s['no_key_needed']} usable with no key at all")
    chk(len(s["disciplines"]) >= 15, f"{len(s['disciplines'])} disciplines covered")
    chk(s["doi_issuing"] >= 8, f"{s['doi_issuing']} issue DOIs (strongest provenance)")

    print("== every entry is complete enough to act on ==")
    for src in SOURCES:
        chk(src.base.startswith("http"), f"{src.key} has a base URL")
        chk(src.auth in ("none", "key", "oauth", "account"),
            f"{src.key} declares its auth type")
        chk(bool(src.licence_posture), f"{src.key} states a licence posture")
        chk(1 <= src.tier <= 4, f"{src.key} has a valid tier")
    chk(all(s_.env for s_ in SOURCES if s_.auth == "key"),
        "every key-authenticated source names its environment variable")

    print("== the four already wired are marked ==")
    impl = {s_.key for s_ in implemented()}
    for k in ("zenodo", "datacite", "openml", "ckan"):
        chk(k in impl, f"{k} is marked implemented")
    chk("materials_project" not in impl,
        "and sources without a parser are NOT marked implemented")

    print("== paid and restricted sources are flagged, not hidden ==")
    paid = [s_ for s_ in SOURCES if "PAID" in s_.licence_posture
            or "NOT redistributable" in s_.licence_posture]
    chk(len(paid) >= 3, f"{len(paid)} sources flagged as paid or restricted")
    iso = next(s_ for s_ in SOURCES if s_.key == "iso_browse")
    chk("NOT the text" in iso.provides,
        "ISO is catalogued for metadata only - clause text stays unpublished")
    chk("copyrighted" in iso.licence_posture, "and says why")

    print("== every credentialed source has somewhere to get the credential ==")
    links = signup_links()
    chk(len(links) >= 40, f"{len(links)} credentials catalogued")
    missing_link = [r["key"] for r in links if r["key"] not in SIGNUP]
    chk(not missing_link,
        f"every keyed source has a signup link ({missing_link[:4]})")
    chk(all(r["signup"].startswith("http") for r in links),
        "and every link is a URL")
    paid_marked = [r for r in links if "PAID" in r["signup"] or "paid" in r["signup"]]
    chk(len(paid_marked) >= 5,
        f"{len(paid_marked)} are marked paid so nobody hunts for a free tier "
        f"that does not exist")

    print("== the novelty check is present ==")
    lit = {s_.key for s_ in by_discipline("literature")}
    chk("openalex" in lit and "semantic_scholar" in lit,
        "literature sources exist for checking whether a finding is new")
    chk("unpaywall" in lit, "and a legal route to full text")

    print(f"\ndata_sources.py: {ok} passed, {fail} failed  "
          f"| {s['total']} sources, {s['implemented']} wired, "
          f"{s['no_key_needed']} keyless")
    return 1 if fail else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    if "--keys" in sys.argv:
        rows = signup_links(only_missing="--missing" in sys.argv)
        print(f"{len(rows)} credentials. Free and instant ones first.\n")
        free = [r for r in rows if "instant" in r["signup"] or "free" in r["signup"].lower()]
        paid = [r for r in rows if r not in free]
        for title, group in (("FREE / FREE TIER", free), ("PAID OR SALES CONTACT", paid)):
            print(f"{'='*78}\n{title}  ({len(group)})\n{'='*78}")
            for r in group:
                print(f"\n  {r['name']}   [{r['discipline']}, tier {r['tier']}]")
                print(f"    unlocks : {r['unlocks'][:66]}")
                print(f"    get key : {r['signup']}")
                print(f"    then    : export {r['env']}=...")
        print(f"\n{'='*78}\nStore them with:  export VAR=value   (or python -c "
              f"\"import api_keys; api_keys.save('name','value')\")")
        print("Check what is configured:  python api_keys.py")
        raise SystemExit(0)
    s = summary()
    print(f"{s['total']} sources | {s['implemented']} wired | "
          f"{s['no_key_needed']} need no key | {s['need_key']} need a key\n")
    for tier in (1, 2, 3, 4):
        rows = by_tier(tier)
        print(f"--- TIER {tier} ({len(rows)}) ---")
        for r in rows:
            mark = "wired" if r.implemented else ("key  " if r.auth != "none" else "open ")
            print(f"  {mark} {r.key:20s} {r.discipline:14s} {r.provides[:52]}")
    print(f"\nenvironment variables to set: {', '.join(s['env_vars'])}")
