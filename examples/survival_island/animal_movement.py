"""Bounded water crossings with a committed route to dry land."""
from collections import deque

WATER_LIMITS = {"bear": 3, "wolf": 1, "deer": 1, "boar": 1, "rabbit": 0}
DIRECTIONS = ((0, -1), (0, 1), (-1, 0), (1, 0))


def passable(world, position):
    return world.passable(position) and world.tile(position).shelter_owner is None


def crossing(world, origin, entry, limit):
    if limit < 1:
        return ()
    queue = deque([(entry, (entry,))])
    visited = {entry}
    while queue:
        pos, route = queue.popleft()
        for dx, dy in DIRECTIONS:
            neighbor = (pos[0] + dx, pos[1] + dy)
            if neighbor == origin or neighbor in visited or not passable(world, neighbor):
                continue
            if not world.tile(neighbor).terrain.is_water:
                return route + (neighbor,)
            if len(route) < limit:
                visited.add(neighbor)
                queue.append((neighbor, route + (neighbor,)))
    return ()


def neighbors(world, animal):
    if world.tile(animal.position).terrain.is_water:
        if animal.water_route:
            return [animal.water_route[0]] if passable(world, animal.water_route[0]) else []
        # Recover safely if a fixture or imported state starts an animal in water.
        route = crossing(world, None, animal.position, WATER_LIMITS[animal.kind])
        animal.water_route = route[1:]
        return [animal.water_route[0]] if animal.water_route else []
    result = []
    for dx, dy in DIRECTIONS:
        pos = (animal.position[0] + dx, animal.position[1] + dy)
        if passable(world, pos) and (not world.tile(pos).terrain.is_water
                or crossing(world, animal.position, pos, WATER_LIMITS[animal.kind])):
            result.append(pos)
    return result


def move(world, animal, target):
    if target == animal.position:
        return
    if not passable(world, target):
        raise ValueError("animals cannot enter an intact shelter")
    if world.tile(target).terrain.is_water and not world.tile(animal.position).terrain.is_water:
        route = crossing(world, animal.position, target, WATER_LIMITS[animal.kind])
        if not route:
            raise ValueError("animal cannot complete this water crossing")
        animal.water_route = route[1:]
    elif animal.water_route and animal.water_route[0] == target:
        animal.water_route = animal.water_route[1:]
    animal.position = target
    if not world.tile(target).terrain.is_water:
        animal.water_route = ()
