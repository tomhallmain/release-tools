from app_release.builders.cargo import CargoBuilder
from app_release.builders.command import CommandBuilder
from app_release.builders.nuitka import NuitkaBuilder

# By the name configuration uses: builder = "<name>".
BUILDERS = {cls.name: cls for cls in (NuitkaBuilder, CommandBuilder, CargoBuilder)}
DEFAULT = NuitkaBuilder.name
