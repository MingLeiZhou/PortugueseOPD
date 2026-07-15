"""Metric projections used by PT60-Candidate reconstruction and audits."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalMetricProjection:
    """Legacy local equirectangular workspace retained for version comparison."""

    lon0: float
    lat0: float
    radius_m: float = 6_371_008.8
    crs_name: str = "LOCAL_EQUIRECTANGULAR_PORTUGAL"

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        x = self.radius_m * math.radians(lon - self.lon0) * math.cos(math.radians(self.lat0))
        y = self.radius_m * math.radians(lat - self.lat0)
        return x, y

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        lon = math.degrees(x / (self.radius_m * math.cos(math.radians(self.lat0)))) + self.lon0
        lat = math.degrees(y / self.radius_m) + self.lat0
        return lon, lat


class PortugalTM06Projection:
    """ETRS89 / Portugal TM06 (EPSG:3763) forward and inverse projection.

    The implementation uses the standard ellipsoidal Transverse Mercator
    series with the EPSG:3763 parameters and the GRS 1980 ellipsoid. It avoids
    adding a runtime dependency solely for the deterministic release builder.
    """

    crs_name = "ETRS89 / Portugal TM06"
    epsg = 3763
    a = 6_378_137.0
    inv_f = 298.257222101
    lat0_deg = 39 + 40 / 60 + 5.73 / 3600
    lon0_deg = -(8 + 7 / 60 + 59.19 / 3600)
    k0 = 1.0
    false_easting = 0.0
    false_northing = 0.0

    def __init__(self) -> None:
        self.f = 1.0 / self.inv_f
        self.e2 = self.f * (2.0 - self.f)
        self.ep2 = self.e2 / (1.0 - self.e2)
        self.lat0 = math.radians(self.lat0_deg)
        self.lon0 = math.radians(self.lon0_deg)
        self.m0 = self._meridional_arc(self.lat0)

    def _meridional_arc(self, phi: float) -> float:
        e2 = self.e2
        return self.a * (
            (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256) * phi
            - (3 * e2 / 8 + 3 * e2**2 / 32 + 45 * e2**3 / 1024) * math.sin(2 * phi)
            + (15 * e2**2 / 256 + 45 * e2**3 / 1024) * math.sin(4 * phi)
            - (35 * e2**3 / 3072) * math.sin(6 * phi)
        )

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        phi = math.radians(lat)
        lam = math.radians(lon)
        sin_phi = math.sin(phi)
        cos_phi = math.cos(phi)
        tan_phi = math.tan(phi)
        n = self.a / math.sqrt(1.0 - self.e2 * sin_phi**2)
        t = tan_phi**2
        c = self.ep2 * cos_phi**2
        aa = cos_phi * (lam - self.lon0)
        m = self._meridional_arc(phi)
        x = self.false_easting + self.k0 * n * (
            aa
            + (1 - t + c) * aa**3 / 6
            + (5 - 18 * t + t**2 + 72 * c - 58 * self.ep2) * aa**5 / 120
        )
        y = self.false_northing + self.k0 * (
            (m - self.m0)
            + n
            * tan_phi
            * (
                aa**2 / 2
                + (5 - t + 9 * c + 4 * c**2) * aa**4 / 24
                + (61 - 58 * t + t**2 + 600 * c - 330 * self.ep2) * aa**6 / 720
            )
        )
        return x, y

    def lonlat(self, x: float, y: float) -> tuple[float, float]:
        e2 = self.e2
        m = self.m0 + (y - self.false_northing) / self.k0
        mu = m / (self.a * (1 - e2 / 4 - 3 * e2**2 / 64 - 5 * e2**3 / 256))
        e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
        phi1 = (
            mu
            + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + 151 * e1**3 / 96 * math.sin(6 * mu)
            + 1097 * e1**4 / 512 * math.sin(8 * mu)
        )
        sin_phi1 = math.sin(phi1)
        cos_phi1 = math.cos(phi1)
        tan_phi1 = math.tan(phi1)
        n1 = self.a / math.sqrt(1 - e2 * sin_phi1**2)
        r1 = self.a * (1 - e2) / (1 - e2 * sin_phi1**2) ** 1.5
        t1 = tan_phi1**2
        c1 = self.ep2 * cos_phi1**2
        d = (x - self.false_easting) / (n1 * self.k0)
        phi = phi1 - (n1 * tan_phi1 / r1) * (
            d**2 / 2
            - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * self.ep2) * d**4 / 24
            + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * self.ep2 - 3 * c1**2) * d**6 / 720
        )
        lam = self.lon0 + (
            d
            - (1 + 2 * t1 + c1) * d**3 / 6
            + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * self.ep2 + 24 * t1**2) * d**5 / 120
        ) / cos_phi1
        return math.degrees(lam), math.degrees(phi)

    def description(self) -> str:
        return "ETRS89 / Portugal TM06 (EPSG:3763), Transverse Mercator, units=m"
