"""Pygame-ce visualization for the survival-island simulation."""

from __future__ import annotations

from .simulation import Resource, Simulation, Terrain
from .reporting import ReportManager


COLORS = {
    Terrain.GRASS: (107, 153, 82),
    Terrain.FOREST: (39, 103, 61),
    Terrain.RIVER: (52, 131, 196),
    Terrain.MOUNTAIN: (104, 105, 110),
}
AGENT_COLORS = [(255, 202, 58), (255, 89, 94), (106, 76, 147), (255, 146, 76), (25, 190, 180)]


def run(*, model: str | None = None, endpoint: str = "http://localhost:11434/api/chat",
        screenshot: str | None = None, report_dir: str | None = None,
        postmortems: bool = True, inference_cooldown: float = 3.0) -> None:
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
        sim.policy = ModelPolicy(sim, model=model, endpoint=endpoint, cooldown=inference_cooldown)
    reports = (ReportManager(sim, directory=report_dir, model=model if postmortems else None,
                             endpoint=endpoint) if not screenshot else None)
    screen = pygame.display.set_mode((sim.world.size * tile_size + sidebar, sim.world.size * tile_size))
    pygame.display.set_caption("bbagents — Survival Island")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("menlo", 14)
    small = pygame.font.SysFont("menlo", 12)
    paused = bool(screenshot)
    ticks_per_second = 8
    accumulator = 0.0
    agent_offset = 0
    running = True

    try:
        while running:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.MOUSEWHEEL:
                    agent_offset = max(0, min(max(0, len(sim.agents) - 5), agent_offset - event.y))
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE:
                        paused = not paused
                    elif event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_r and reports:
                        reports.save()
                    elif event.key == pygame.K_RIGHT and paused:
                        sim.step()
                    elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                        ticks_per_second = min(40, ticks_per_second + 2)
                    elif event.key == pygame.K_MINUS:
                        ticks_per_second = max(1, ticks_per_second - 2)
                    elif event.key == pygame.K_PAGEDOWN:
                        agent_offset = min(max(0, len(sim.agents) - 5), agent_offset + 1)
                    elif event.key == pygame.K_PAGEUP:
                        agent_offset = max(0, agent_offset - 1)

            if not paused:
                accumulator += dt
                interval = 1.0 / ticks_per_second
                while accumulator >= interval:
                    sim.step()
                    accumulator -= interval

            if reports:
                reports.update()

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

            if sim.last_lightning and sim.tick - sim.last_lightning[1] < 8:
                (lx, ly), _ = sim.last_lightning
                x, y = lx * tile_size, ly * tile_size
                pygame.draw.lines(screen, (255, 240, 100), False,
                                  [(x + 15, y + 1), (x + 6, y + 12), (x + 14, y + 10), (x + 5, y + 21)], 3)

            for predator in sim.predators:
                if predator.alive:
                    px, py = predator.position
                    rect = pygame.Rect(px * tile_size + 3, py * tile_size + 3, tile_size - 6, tile_size - 6)
                    pygame.draw.rect(screen, (205, 50, 45) if predator.kind == "wolf" else (100, 49, 27), rect)
                    label = small.render("W" if predator.kind == "wolf" else "B", True, (255, 255, 255))
                    screen.blit(label, label.get_rect(center=rect.center))

            for index, agent in enumerate(sim.agents):
                x, y = agent.position
                center = (x * tile_size + tile_size // 2, y * tile_size + tile_size // 2)
                pygame.draw.circle(screen, AGENT_COLORS[index % len(AGENT_COLORS)] if agent.alive else (30, 30, 30), center, 7)
                label = small.render(agent.name[0], True, (10, 10, 10))
                screen.blit(label, label.get_rect(center=center))
                if agent.sleep_remaining:
                    screen.blit(small.render("z", True, (255, 255, 255)), (center[0] + 5, center[1] - 14))

            left = sim.world.size * tile_size + 14
            living_predators = sum(predator.alive for predator in sim.predators)
            status = f"Day {sim.tick // sim.day_length + 1} · {'Night' if sim.is_night else 'Daylight'} · Tick {sim.tick}"
            screen.blit(font.render(status, True, (235, 235, 235)), (left, 10))
            speed = "Paused" if paused else f"Speed {ticks_per_second} ticks/s"
            population = sum(agent.alive for agent in sim.agents)
            screen.blit(small.render(f"{speed} · Alive {population}/{len(sim.agents)} · Predators {living_predators} · Scroll", True, (150, 155, 160)), (left, 31))
            if model:
                summary = f"LLM {sim.policy.accepted} plans / {sim.policy.cache_hits} cached / {sim.policy.failed} failed"
                screen.blit(small.render(summary, True, (190, 210, 230)), (left, 45))
            y = 64
            for index, agent in enumerate(sim.agents[agent_offset:agent_offset + 5], start=agent_offset):
                color = AGENT_COLORS[index % len(AGENT_COLORS)]
                screen.blit(font.render(f"{agent.name} — {agent.objective}", True, color), (left, y))
                y += 18
                needs = f"HP {agent.health:3.0f}  hunger {agent.hunger:3.0f}  thirst {agent.thirst:3.0f}  energy {agent.energy:3.0f}"
                screen.blit(small.render(needs, True, (210, 210, 210)), (left, y))
                y += 16
                inventory = ", ".join(f"{name}:{amount}" for name, amount in agent.inventory.items() if amount) or "empty"
                screen.blit(small.render(f"INT {agent.intelligence} · inventory: {inventory}", True, (180, 185, 190)), (left, y))
                y += 16
                plan = (agent.current_plan if agent.suspended_plan_key else
                        getattr(sim.policy, "model_plans", {}).get(agent.id, agent.current_plan))
                ready = (f"Sleeping ({agent.sleep_remaining} ticks left)" if agent.sleep_remaining else
                         plan.next_ready_step().title if plan and plan.next_ready_step() else "complete")
                screen.blit(small.render(f"plan v{plan.version if plan else 0}: {ready}", True, (180, 185, 190)), (left, y))
                y += 16
                action = (agent.last_action if agent.sleep_remaining else
                          getattr(sim.policy, "status", {}).get(agent.id, agent.last_action))[:54]
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
    except KeyboardInterrupt:
        pass
    finally:
        pygame.quit()
        if hasattr(sim.policy, "close"):
            sim.policy.close()
        if reports:
            reports.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Survival Island")
    parser.add_argument("--model", help="Ollama model name; omit for reference policy")
    parser.add_argument("--endpoint", default="http://localhost:11434/api/chat")
    parser.add_argument("--screenshot", help="Save a rendered frame after 120 reference-policy ticks and exit")
    parser.add_argument("--report-dir", help="Directory for saved survivor histories (default: unique survival-runs subdirectory)")
    parser.add_argument("--no-postmortem", action="store_true", help="Save traces without generating model death summaries")
    parser.add_argument("--inference-cooldown", type=float, default=3.0,
                        help="Minimum idle seconds between model calls (default: 3)")
    args = parser.parse_args()
    run(model=args.model, endpoint=args.endpoint, screenshot=args.screenshot,
        report_dir=args.report_dir, postmortems=not args.no_postmortem,
        inference_cooldown=args.inference_cooldown)
