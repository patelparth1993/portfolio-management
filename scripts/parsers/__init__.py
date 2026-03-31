from .wealthsimple import parse as parse_wealthsimple
from .wealthsimple import apply_gemini_response as ws_apply_gemini
from .manulife import parse as parse_manulife
from .manulife import apply_gemini_response as ml_apply_gemini

PARSERS = {
    "wealthsimple": parse_wealthsimple,
    "manulife": parse_manulife,
}

# Maps institution → function that merges a Gemini text response into result(s)
GEMINI_APPLIERS = {
    "wealthsimple": ws_apply_gemini,
    "manulife": ml_apply_gemini,
}
