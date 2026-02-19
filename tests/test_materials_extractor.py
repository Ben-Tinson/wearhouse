from services.materials_extractor import extract_materials


def test_extract_materials_leather_suede():
    result = extract_materials("Leather upper with suede overlays.")
    assert result["primary_material"] == "Leather"
    assert "Leather" in result["materials"]
    assert "Suede" in result["materials"]
    assert result["confidence"] >= 0.7


def test_extract_materials_flyknit_maps_to_knit():
    result = extract_materials("Lightweight Flyknit construction.")
    assert result["primary_material"] == "Knit"
    assert "Knit" in result["materials"]
