from .wealthsimple import parse as parse_wealthsimple
from .manulife import parse as parse_manulife

PARSERS = {
    "wealthsimple": parse_wealthsimple,
    "manulife": parse_manulife,
}
