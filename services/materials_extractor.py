import re
from typing import Dict, List, Optional


MATERIAL_PRIORITY = [
    "patent leather",
    "tumbled leather",
    "leather",
    "suede",
    "nubuck",
    "knit",
    "mesh",
    "canvas",
    "denim",
    "nylon",
    "polyester",
    "satin",
    "silk",
    "corduroy",
    "gore-tex",
    "neoprene",
    "rubber",
    "foam",
    "cork",
    "synthetic",
    "plastic",
    "tpu",
    "eva foam",
]

VAGUE_MATERIALS = {"synthetic", "plastic", "foam"}
DISPLAY_NAMES = {
    "patent leather": "Patent Leather",
    "tumbled leather": "Tumbled Leather",
    "leather": "Leather",
    "suede": "Suede",
    "nubuck": "Nubuck",
    "knit": "Knit",
    "mesh": "Mesh",
    "canvas": "Canvas",
    "denim": "Denim",
    "nylon": "Nylon",
    "polyester": "Polyester",
    "satin": "Satin",
    "silk": "Silk",
    "corduroy": "Corduroy",
    "gore-tex": "Gore-Tex",
    "neoprene": "Neoprene",
    "rubber": "Rubber",
    "foam": "Foam",
    "cork": "Cork",
    "synthetic": "Synthetic",
    "plastic": "Plastic",
    "tpu": "TPU",
    "eva foam": "EVA Foam",
}


def _has_word(text: str, word: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}\b", text))


def extract_materials(text: Optional[str], source: Optional[str] = None) -> Dict[str, object]:
    if not text:
        return {
            "primary_material": None,
            "materials": [],
            "confidence": 0.0,
            "source": source,
        }

    normalized = " ".join(text.lower().split())
    materials: List[str] = []

    def add(material: str) -> None:
        if material not in materials:
            materials.append(material)

    if "patent" in normalized and "leather" in normalized:
        add("patent leather")
    if "tumbled leather" in normalized:
        add("tumbled leather")

    keyword_map = {
        "leather": ["leather"],
        "suede": ["suede"],
        "nubuck": ["nubuck"],
        "knit": ["knit", "flyknit", "primeknit"],
        "mesh": ["mesh"],
        "canvas": ["canvas"],
        "denim": ["denim"],
        "rubber": ["rubber"],
        "foam": ["foam"],
        "eva foam": ["eva foam", "eva"],
        "synthetic": ["synthetic"],
        "plastic": ["plastic"],
        "tpu": ["tpu"],
        "nylon": ["nylon"],
        "polyester": ["polyester"],
        "satin": ["satin"],
        "silk": ["silk"],
        "corduroy": ["corduroy"],
        "gore-tex": ["gore-tex", "gore tex"],
        "neoprene": ["neoprene"],
        "cork": ["cork"],
    }

    for material, keywords in keyword_map.items():
        for keyword in keywords:
            if _has_word(normalized, keyword):
                add(material)
                break

    if not materials:
        return {
            "primary_material": None,
            "materials": [],
            "confidence": 0.0,
            "source": source,
        }

    primary_material = None
    for material in MATERIAL_PRIORITY:
        if material in materials:
            primary_material = material
            break

    if len(materials) >= 2:
        confidence = 0.9
    elif materials[0] in VAGUE_MATERIALS:
        confidence = 0.4
    else:
        confidence = 0.7

    ordered_materials = [material for material in MATERIAL_PRIORITY if material in materials]
    display_materials = [DISPLAY_NAMES.get(material, material.title()) for material in ordered_materials]
    display_primary = DISPLAY_NAMES.get(primary_material, primary_material.title()) if primary_material else None
    return {
        "primary_material": display_primary,
        "materials": display_materials,
        "confidence": confidence,
        "source": source,
    }
