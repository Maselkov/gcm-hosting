"""Bases' names, made up rather than chosen: <adjective>-<noun>-<4 digits>,
as copper-falcon-4821. Nobody can take a name like "official" or pass
for someone else, nothing offensive comes up, and a deleted base's name
isn't reused for long. With the wildcard certificate keeping names out
of the public certificate logs, a base's name can't be listed or
guessed either: 256 x 256 x 10,000 is about 655 million. That's no
access control - whoever has the link has the name - only that nobody
finds a base without it.

The words are plain on their own and in any pair: materials, colours,
weather and the like, and animals, places and things, no people."""

import secrets

ADJECTIVES = """
amber ancient arctic ashen autumn azure balmy bold brass brave breezy
bright brisk bronze calm candid carved cedar chalk cheery chilly chrome
civic clear clever cloudy cobalt coastal cobbled copper coral cosmic
cozy crimson crisp crystal curious dapper daring dawn deep dewy distant
dusky dusty eager early earthen eastern electric emerald even fabled
fair faithful feathered fern fiery flint floral foggy forest frosty
gentle gilded glacial glassy gleaming glowing golden granite grassy
hardy hazel hidden hollow honest humble icy indigo iron ivory jade
jolly jovial keen kind lapis lavender leafy lime little lively lofty
loyal lucky lunar magnetic maple marble meadow mellow merry midnight
mighty minty misty modest molten mossy nimble noble northern oaken
ocean olive onyx opal orange orchid pale patient pebbled pine plucky
polar polished proud quartz quick quiet radiant rainy rapid rocky
rosy royal ruby rugged russet rustic sable sandy sapphire scarlet
serene shady shining silent silken silver simple sleek slate snowy
solar solid sonic spare speedy spring spruce starry steady steel stone
stormy sturdy sublime sunny swift tawny teal tender thorny tidal
timber tiny topaz tranquil tropic true tundra twilight upper valiant
velvet verdant vivid wandering warm watchful wavy western whispering
wild willow windy winter wise witty wooden woven young zealous zesty
zinc auburn boreal carmine cerulean cinnamon harvest ironclad jubilant
lush obsidian calico canvas copse dappled drifting elder fleet frozen
gusty harbor heather highland inland jasper kindly lantern marsh
mineral nectar nordic nutmeg oasis pewter prairie quill ridge
river saffron salty sienna sterling summit thistle tin umber valley
vapor vernal ruddy
""".split()  # noqa: SIM905 - easier to read and edit than a list

NOUNS = """
acorn albatross alcove anchor anvil apple arch atlas aurora badger
beacon bear beaver beetle bell birch bison boulder bramble bridge
brook buffalo burrow butte cabin canyon cardinal caribou cascade
castle cavern cedar chamber chapel cheetah citadel cliff clover
cobra comet condor coral cove coyote crane crater creek crow crystal
cypress delta dolphin dove dragon drake dune eagle echo egret elk
ember falcon fern ferret field finch fjord flame forge fortress fox
gale garden gazelle geyser glacier glade gorge grove gull harbor
hare hawk heron hill hollow horizon hornet ibis island jackal jaguar
kestrel kettle kiln knoll koala lagoon lake lantern lark ledge lemur
lighthouse lily lynx magpie mammoth manor maple marsh meadow mesa
meteor mill mink mole moose moth mountain narwhal nebula nest newt
oak oasis ocelot orchard oriole osprey otter owl panda panther parrot
peak pelican penguin pine plateau plover pond poppy prairie puffin
quail quarry rabbit raven reef ridge river robin rook salmon
savanna seal sequoia shore sparrow spire spring spruce squirrel stag
star stream summit swallow swan thicket thrush tiger timber toad
tower trail tundra turtle valley vault viper vista vole walrus warbler
weasel whale willow wolf wombat wren yak zebra basalt
bastion bobcat cairn canopy capstone channel chimney compass
crag dynamo engine falls foundry furnace gantry grotto hamlet hatch
haven heath kraken loom marten monolith avalanche beryl bluebell canal
causeway citrine ermine fountain gopher granary hedgehog inlet marmot
mongoose nautilus orbit pasture pinnacle starling tapir outpost pagoda pylon
quasar reactor rampart refinery relay rotor sentinel spindle station
terrace turbine valve wharf workshop
""".split()  # noqa: SIM905 - easier to read and edit than a list


def generate():
    return f"{secrets.choice(ADJECTIVES)}-{secrets.choice(NOUNS)}-{secrets.randbelow(10000):04d}"
