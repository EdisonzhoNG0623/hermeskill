from hermeskill.capability import (
    CapabilityInventory,
)


def test_inventory_load():

    inventory = CapabilityInventory(
        "config/capability-inventory.yaml"
    )


    assert (
        "docker.restart"
        in inventory.all()
    )


    item = inventory.get(
        "execution.l3"
    )


    assert item["risk"] == "high"
