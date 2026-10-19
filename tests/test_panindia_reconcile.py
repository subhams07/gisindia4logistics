"""reconcile_districts must not dissolve mis-coded sub-districts into duplicate districts."""
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

import scripts.clean.process_panindia_boundaries as panindia

DATA = Path(__file__).resolve().parents[1] / "data" / "administrative"


def frame(rows):
    return gpd.GeoDataFrame([{k: v for k, v in r.items() if k != "geometry"} for r in rows],
                            geometry=[r["geometry"] for r in rows], crs=4326)


def district(state, name, code, geometry):
    return dict(state=state, state_code=27, district=name, district_code=code, remarks=None, geometry=geometry)


def subdistrict(state, name, code, geometry):
    return dict(state=state, state_code=27, district=name, district_code=code, sub_district="S",
                sub_district_code=1, sub_district_type=None, remarks=None, geometry=geometry)


DISTRICTS = frame([district("Kerala", "Alappuzha", 598, box(76, 9, 77, 10)),
                   district("Maharashtra", "DHULE", 498, box(74, 20, 75, 21))])


@pytest.mark.parametrize("sub", [
    subdistrict("Maharashtra", "DHULE", 598, box(74, 20, 75, 21)),  # name exists and code is Kerala's
    subdistrict("Maharashtra", "DHULE", 999, box(74, 20, 75, 21)),  # name exists in the state
    subdistrict("Maharashtra", "NEW", 598, box(74, 20, 75, 21)),    # code belongs to another state
])
def test_collisions_raise_instead_of_deriving(monkeypatch, sub):
    monkeypatch.setattr(panindia, "process", lambda layer: frame([sub]))
    with pytest.raises(ValueError, match="SUBDISTRICT_DISTRICT_CODE_FIXES"):
        panindia.reconcile_districts(DISTRICTS)


def test_genuinely_new_district_is_derived(monkeypatch):
    monkeypatch.setattr(panindia, "process", lambda layer: frame([
        subdistrict("Maharashtra", "NEW", 700, box(73, 19, 73.5, 19.5)),
        subdistrict("Maharashtra", "NEW", 700, box(73.5, 19, 74, 19.5))]))
    out = panindia.reconcile_districts(DISTRICTS)
    assert len(out) == 3
    new = out[out.district_code == 700].iloc[0]
    assert new.district == "NEW" and new.geometry.area == pytest.approx(0.5)


def test_committed_layers_carry_dhule_once_as_498():
    districts = gpd.read_file(DATA / "india_districts_lgd.geojson")
    subdistricts = gpd.read_file(DATA / "india_subdistricts_lgd.gpkg")
    assert len(districts) == 780
    assert districts.loc[districts.district == "DHULE", "district_code"].tolist() == [498]
    assert districts.loc[districts.district_code == 598, "district"].tolist() == ["Alappuzha"]
    dhule = subdistricts[subdistricts.district == "DHULE"]
    assert sorted(dhule.sub_district) == ["DHULE", "SAKRI", "SHIRPUR", "SINDKHEDE"]
    assert set(dhule.district_code) == {498}
    # every coded sub-district joins to a district of the same state
    have = set(zip(districts.state, districts.district_code))
    coded = subdistricts.dropna(subset=["district_code"])
    assert all(pair in have for pair in zip(coded.state, coded.district_code))
