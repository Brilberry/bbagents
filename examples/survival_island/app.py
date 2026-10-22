"""Pygame-ce visualization for the survival-island simulation."""

from __future__ import annotations

from .simulation import Resource, Simulation, Terrain


COLORS = {
    Terrain.GRASS: (107, 153, 82),
    Terrain.FOREST: (39, 103, 61),
    Terrain.RIVER: (52, 131, 196),
    Terrain.MOUNTAIN: (104, 105, 110),
}
AGENT_COLORS = [(255, 202, 58), (255, 89, 94), (106, 76, 147), (255, 146, 76), (25, 190, 180)]


def run(*, model: str | None = None, endpoint: str = "http://localhost:11434/api/chat",
        screenshot: str | None = None) -> None:
    try:
        import pygame
    except ImportError as exc:
        raise SystemExit("Install the renderer with: pip install pygame-ce") from exc

    pygame.init()
    tile_size = 22
    sidebar = 440
    sim = Simulation()
    if screenshot:
        for _ in range(120):
            sim.step()
    if model:
        from .model_policy import ModelPolicy
        sim.policy = ModelPolicy(sim, model=model, endpoint=endpoint)
    screen = pygame.display.set_mode((sim.world.size * tile_size + sidebar, sim.world.size * tile_size))
    pygame.display.set_caption("bbagents — Survival Island")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("menlo", 14)
    small = pygame.font.SysFont("menlo", 12)
    paused = bool(screenshot)
    ticks_per_second = 8
    accumulator = 0.0
    running = True

    while running:
        dt = clock.tick(60) / 1000.0
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_SPACE:
                    paused = not paused
                elif event.key == pygame.K_RIGHT and paused:
                    sim.step()
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                    ticks_per_second = min(40, ticks_per_second + 2)
                elif event.key == pygame.K_MINUS:
                    ticks_per_second = max(1, ticks_per_second - 2)

        if not paused:
            accumulator += dt
            interval = 1.0 / ticks_per_second
            while accumulator >= interval:
                sim.step()
                accumulator -= interval

        screen.fill((20, 22, 25))
        for y, row in enumerate(sim.world.tiles):
            for x, tile in enumerate(row):
                rect = pygame.Rect(x * tile_size, y * tile_size, tile_size, tile_size)
                pygame.draw.rect(screen, COLORS[tile.terrain], rect)
                pygame.draw.rect(screen, (25, 30, 32), rect, 1)
                if tile.shelter_owner:
                    pygame.draw.polygon(screen, (116, 72, 42), [(rect.left + 3, rect.bottom - 3), (rect.centerx, rect.top + 3), (rect.right - 3, rect.bottom - 3)])
                elif tile.resources[Resource.BERRIES.value] > 0:
                    pygame.draw.circle(screen, (196, 54, 92), rect.center, 2)
                elif tile.resources[Resource.STONE.value] > 0:
                    pygame.draw.circle(screen, (210, 210, 205), rect.center, 3)

        for index, agent in enumerate(sim.agents):
            x, y = agent.position
            center = (x * tile_size + tile_size // 2, y * tile_size + tile_size // 2)
            pygame.draw.circle(screen, AGENT_COLORS[index % len(AGENT_COLORS)] if agent.alive else (30, 30, 30), center, 7)
            label = small.render(agent.name[0], True, (10, 10, 10))
            screen.blit(label, label.get_rect(center=center))

        left = sim.world.size * tile_size + 14
        status = f"Tick {sim.tick}  {'NIGHT' if sim.is_night else 'DAY'}  {'PAUSED' if paused else f'{ticks_per_second} t/s'}"
        screen.blit(font.render(status, True, (235, 235, 235)), (left, 10))
        screen.blit(small.render("SPACE pause · → step · +/- speed", True, (150, 155, 160)), (left, 31))
        if model:
            summary = f"LLM {sim.policy.accepted} accepted / {sim.policy.failed} failed"
            screen.blit(small.render(summary, True, (190, 210, 230)), (left, 45))
        y = 64
        for index, agent in enumerate(sim.agents):
            color = AGENT_COLORS[index % len(AGENT_COLORS)]
            screen.blit(font.render(f"{agent.name} — {agent.objective}", True, color), (left, y))
            y += 18
            needs = f"HP {agent.health:3.0f}  hunger {agent.hunger:3.0f}  thirst {agent.thirst:3.0f}  energy {agent.energy:3.0f}"
            screen.blit(small.render(needs, True, (210, 210, 210)), (left, y))
            y += 16
            inventory = ", ".join(f"{name}:{amount}" for name, amount in agent.inventory.items() if amount) or "empty"
            screen.blit(small.render(f"inventory: {inventory}", True, (180, 185, 190)), (left, y))
            y += 16
            plan = getattr(sim.policy, "model_plans", {}).get(agent.id, agent.current_plan)
            ready = plan.next_ready_step().title if plan and plan.next_ready_step() else "complete"
            screen.blit(small.render(f"plan v{plan.version if plan else 0}: {ready}", True, (180, 185, 190)), (left, y))
            y += 16
            action = getattr(sim.policy, "status", {}).get(agent.id, agent.last_action)[:54]
            screen.blit(small.render(action, True, (145, 180, 145)), (left, y))
            y += 24

        screen.blit(font.render("Events", True, (235, 235, 235)), (left, y))
        y += 19
        for line in list(sim.event_log)[:7]:
            screen.blit(small.render(line[:59], True, (160, 165, 170)), (left, y))
            y += 16

        pygame.display.flip()
        if screenshot:
            pygame.image.save(screen, screenshot)
            running = False

    pygame.quit()
    if hasattr(sim.policy, "close"):
        sim.policy.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Survival Island")
    parser.add_argument("--model", help="Ollama model name; omit for reference policy")
    parser.add_argument("--endpoint", default="http://localhost:11434/api/chat")
    parser.add_argument("--screenshot", help="Save a rendered frame after 120 reference-policy ticks and exit")
    args = parser.parse_args()
    run(model=args.model, endpoint=args.endpoint, screenshot=args.screenshot)
