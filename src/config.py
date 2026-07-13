from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
METADATA_DIR = DATA_DIR / "metadata"
SAMPLES_DIR = DATA_DIR / "samples"
REPORTS_DIR = ROOT_DIR / "reports"
NOTEBOOKS_DIR = ROOT_DIR / "notebooks"
LOG_DIR = ROOT_DIR / "logs"

BASE_URL = "https://e-redes.opendatasoft.com"
TIMEZONE = "Europe/Lisbon"
SAMPLE_RECORDS = 1000
PAGE_SIZE = 100
REQUEST_TIMEOUT = 60
MAX_RETRIES = 4
BACKOFF_SECONDS = 2

# Full exports larger than this are documented and left for explicit
# resumable pagination with --force-large.
FULL_DOWNLOAD_RECORD_LIMIT = 200_000

PAGE_URLS = {
    "rnd": "https://e-redes.opendatasoft.com/pages/rnd/",
    "rari": "https://e-redes.opendatasoft.com/pages/caracterizacao_redes_distribuicao/",
    "quality": "https://e-redes.opendatasoft.com/pages/qualidade_energia_eletrica/",
}

DIRECT_DATASETS = {
    "carga-na-subestacao": "Carga na subestacao",
    "caracteristicas-da-rede": "Caracteristicas da rede",
    "capacidade-rececao-rnd": "Capacidade de rececao da RND",
    "diagrama-de-carga-de-subestacao": "Diagrama de carga de subestacao index",
    "qualidade_energia_sobretensoes-final": "Qualidade de energia - sobretensoes",
    "qualidade_energia_cavas-final": "Qualidade de energia - cavas de tensao",
    "qualidade_energia_fenomenoscontinuos-final": "Qualidade de energia - fenomenos continuos",
    "12-continuidade-de-servico-indicadores-gerais-de-continuidade-de-servico": "Indicadores continuidade de servico",
}

KNOWN_LOAD_SPLITS = {
    "diagrama_carga_subestacao_01_a_07",
    "diagrama_carga_subestacao_08_a_10",
    "diagrama_carga_subestacao_11_a_12",
    "diagrama_carga_subestacao_13_a_15",
    "diagrama_carga_subestacao_16_a_18",
}

SUPPORTING_GEO_DATASETS = {
    "districts-portugal",
    "municipalities-portugal",
    "civil-parishes-portugal",
}

EXPORT_FORMATS = ("csv", "json", "geojson", "shp", "parquet")

ELECTRICAL_KEYWORDS = (
    "tensao",
    "voltage",
    "potencia",
    "power",
    "carga",
    "load",
    "energia",
    "energy",
    "capacidade",
    "capacity",
    "corrente",
    "current",
    "curto",
    "short",
    "transform",
    "subestacao",
    "instalacao",
)

SUBSTATION_KEYWORDS = (
    "subestacao",
    "substation",
    "codigo_da_instalacao",
    "codigo_subestacao",
    "instalacao",
)

LOCATION_KEYWORDS = (
    "geo",
    "coordenad",
    "lat",
    "lon",
    "distrito",
    "concelho",
    "freguesia",
    "municip",
    "parish",
    "district",
)

DATE_KEYWORDS = ("data", "hora", "date", "time", "ano", "year")

VOLTAGE_KEYWORDS = ("tensao", "voltage", "kv")
