"""Selecciones nacionales: cómo las abrevian las casas (Draftea: «ESP», «ING», «ALE», «HOL») frente a cómo las nombra The Odds API en inglés.

Las abreviaturas de un CLUB se derivan de su nombre (catalogo.codigos_de), pero las de una SELECCIÓN no: «Spain» no empieza con «ESP», «Germany» no da «ALE».
Esta tabla las une. Dos tipos de dato:
  * códigos FIFA oficiales (TUR, BEL, CZE, ESP, SMR, BLR, …): estándar público;
  * variantes en ESPAÑOL que usa la casa, confirmadas con una captura real de Draftea: ING (Inglaterra), ALE (Alemania), HOL (Países Bajos/Holanda), PRT (Portugal),
    RKS (Kosovo). Otras variantes en español (SUE, DIN, ESC, GAL…) son plausibles pero NO están verificadas con la casa: se aceptan solo como candidatas, y aun así un par
    de abreviaturas solo se acepta si entre los partidos programados hay UNO donde ambos encajan (catalogo.emparejar_flexible). Un código que falte se agrega como alias manual.

No es un modelo ni una fuente de datos deportivos: es solo la equivalencia de nombres (homologación) entre la casa y la API.
"""
from __future__ import annotations

import re
import unicodedata

# (nombres en inglés tal como pueden llegar de The Odds API, códigos aceptados)
_TABLA: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("Albania",), ("ALB",)), (("Andorra",), ("AND",)), (("Armenia",), ("ARM",)), (("Austria",), ("AUT",)), (("Azerbaijan",), ("AZE",)),
    (("Belarus",), ("BLR",)), (("Belgium",), ("BEL",)), (("Bosnia and Herzegovina", "Bosnia & Herzegovina", "Bosnia-Herzegovina", "Bosnia"), ("BIH",)),
    (("Bulgaria",), ("BUL",)), (("Croatia",), ("CRO",)), (("Cyprus",), ("CYP",)), (("Czech Republic", "Czechia"), ("CZE",)),
    (("Denmark",), ("DEN", "DIN")), (("England",), ("ENG", "ING")), (("Estonia",), ("EST",)), (("Faroe Islands", "Faroe Is"), ("FRO",)),
    (("Finland",), ("FIN",)), (("France",), ("FRA",)), (("Georgia",), ("GEO",)), (("Germany",), ("GER", "ALE")), (("Gibraltar",), ("GIB",)),
    (("Greece",), ("GRE",)), (("Hungary",), ("HUN",)), (("Iceland",), ("ISL", "ICE")), (("Ireland", "Republic of Ireland", "Rep of Ireland"), ("IRL",)),
    (("Israel",), ("ISR",)), (("Italy",), ("ITA",)), (("Kazakhstan",), ("KAZ",)), (("Kosovo",), ("KOS", "RKS")), (("Latvia",), ("LVA", "LAT")),
    (("Liechtenstein",), ("LIE",)), (("Lithuania",), ("LTU", "LIT")), (("Luxembourg",), ("LUX",)), (("Malta",), ("MLT", "MAL")), (("Moldova",), ("MDA",)),
    (("Montenegro",), ("MNE",)), (("Netherlands", "Holland"), ("NED", "HOL")), (("North Macedonia", "Macedonia"), ("MKD",)), (("Northern Ireland",), ("NIR",)),
    (("Norway",), ("NOR",)), (("Poland",), ("POL",)), (("Portugal",), ("POR", "PRT")), (("Romania",), ("ROU", "ROM")), (("San Marino",), ("SMR",)),
    (("Scotland",), ("SCO", "ESC")), (("Serbia",), ("SRB",)), (("Slovakia",), ("SVK",)), (("Slovenia",), ("SVN",)), (("Spain",), ("ESP",)),
    (("Sweden",), ("SWE", "SUE")), (("Switzerland",), ("SUI",)), (("Turkey", "Turkiye"), ("TUR",)), (("Ukraine",), ("UKR",)), (("Wales",), ("WAL", "GAL")),
    # fuera de Europa (códigos FIFA)
    (("Argentina",), ("ARG",)), (("Brazil",), ("BRA",)), (("Uruguay",), ("URU",)), (("Colombia",), ("COL",)), (("Chile",), ("CHI", "CHL")),
    (("Peru",), ("PER",)), (("Ecuador",), ("ECU",)), (("Paraguay",), ("PAR",)), (("Bolivia",), ("BOL",)), (("Venezuela",), ("VEN",)),
    (("Mexico",), ("MEX",)), (("USA", "United States", "United States of America"), ("USA", "EUA")), (("Canada",), ("CAN",)),
    (("Costa Rica",), ("CRC",)), (("Panama",), ("PAN",)), (("Honduras",), ("HON",)), (("Jamaica",), ("JAM",)),
    (("Japan",), ("JPN", "JAP")), (("South Korea", "Korea Republic"), ("KOR", "COR")), (("Australia",), ("AUS",)),
    (("Morocco",), ("MAR",)), (("Senegal",), ("SEN",)), (("Egypt",), ("EGY",)), (("Nigeria",), ("NGA",)), (("Ghana",), ("GHA",)),
    (("Cameroon",), ("CMR",)), (("Saudi Arabia",), ("KSA",)), (("Iran",), ("IRN",)), (("Qatar",), ("QAT",)),
)


def _clave(t: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_POR_NOMBRE: dict[str, frozenset[str]] = {}
for _nombres, _codigos in _TABLA:
    for _n in _nombres:
        _POR_NOMBRE[_clave(_n)] = frozenset(_codigos)


def codigos(nombre: str) -> frozenset[str]:
    """Códigos de 3 letras de una selección por su nombre en inglés (vacío si no es una selección de la tabla)."""
    return _POR_NOMBRE.get(_clave(nombre), frozenset())
