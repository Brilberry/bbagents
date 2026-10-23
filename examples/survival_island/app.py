"""Pygame-ce visualization for the survival-island simulation."""

from __future__ import annotations

from .simulation import Resource, Simulation, Terrain
from .reporting import ReportManager
from .report_view import PostmortemExport


COLORS = {
    Terrain.GRASS: (107, 153, 82),
    Terrain.FOREST: (39, 103, 61),
    Terrain.LAKE: (52, 131, 196),
    Terrain.RIVER: (52, 131, 196),
    Terrain.MOUNTAIN: (104, 105, 110),
}
AGENT_COLORS = [(255, 202, 58), (255, 89, 94), (106, 76, 147), (255, 146, 76), (25, 190, 180)]
HELP_SHORTCUTS = (
    ("H", "Show / hide keyboard help"),
    ("Esc", "Close help; otherwise exit and save"),
    ("Space", "Pause / resume simulation"),
    ("Right arrow", "Advance one tick while paused"),
    ("+ or =", "Increase simulation speed (up to 40 ticks/s)"),
    ("-", "Decrease simulation speed (down to 1 tick/s)"),
    ("Page Up / Down", "Scroll the survivor list (mouse wheel also works)"),
    ("R", "Save a Markdown report snapshot"),
    ("Enter", "Generate / open postmortem at Game Over"),
    ("Ctrl+C", "Exit from the terminal and save reports"),
)


def run(*, model: str | None = None, endpoint: str = "http://localhost:11434/api/chat",
        screenshot: str | None = None, report_dir: str | None = None,
        postmortems: bool = True, inference_cooldown: float = 3.0,
        team: str | None = None, competition: str | None = None, seed: int | None = None,
        starting_season: str | None = None) -> None:
    if bool(team) != bool(competition):
        raise ValueError("--team and --competition must be supplied together")
    if competition and model:
        raise ValueError("competition v1 supports rules-only decisions")
    if competition and starting_season is not None:
        raise ValueError("set starting_season in the competition JSON, not as a CLI override")
    try:
        import pygame
    except ImportError as exc:
        raise SystemExit("Install the renderer with: pip install pygame-ce") from exc

    pygame.init()
    tile_size = 22
    sidebar = 440
    if competition:
        from .competition import create_simulation, load
        settings = load(competition)
        sim = create_simulation(load(team, json5=True), settings,
                                settings["seeds"][0] if seed is None else seed)
    else:
        sim = (Simulation() if seed is None and starting_season is None else
               Simulation(seed=7 if seed is None else seed, starting_season=starting_season or "spring"))
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
    game_over_handled = False
    reports_closed = False
    help_open = False
    help_dialog = pygame.Rect(0, 0, 700, 430)
    help_dialog.center = screen.get_rect().center
    help_close = pygame.Rect(help_dialog.right - 140, help_dialog.bottom - 55, 110, 36)
    export = PostmortemExport(reports) if reports else None
    dialog = pygame.Rect(0, 0, 650, 240)
    dialog.center = screen.get_rect().center
    generate_button = pygame.Rect(dialog.left + 30, dialog.bottom - 65, 300, 42)
    exit_button = pygame.Rect(dialog.right - 150, dialog.bottom - 65, 120, 42)

    try:
        while running:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_h:
                    help_open = not help_open
                elif help_open:
                    if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                        help_open = False
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and help_close.collidepoint(event.pos):
                        help_open = False
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and sim.game_over and not any(agent.alive for agent in sim.agents):
                    if generate_button.collidepoint(event.pos) and export:
                        export.start()
                    elif exit_button.collidepoint(event.pos):
                        running = False
                elif event.type == pygame.MOUSEWHEEL:
                    agent_offset = max(0, min(max(0, len(sim.agents) - 5), agent_offset - event.y))
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_SPACE and not sim.game_over:
                        paused = not paused
                    elif event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_RETURN and sim.game_over and export and not any(agent.alive for agent in sim.agents):
                        export.start()
                    elif event.key == pygame.K_r and reports and not (export and export.busy):
                        reports.save()
                    elif event.key == pygame.K_RIGHT and paused and not sim.game_over:
                        sim.step()
                    elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                        ticks_per_second = min(40, ticks_per_second + 2)
                    elif event.key == pygame.K_MINUS:
                        ticks_per_second = max(1, ticks_per_second - 2)
                    elif event.key == pygame.K_PAGEDOWN:
                        agent_offset = min(max(0, len(sim.agents) - 5), agent_offset + 1)
                    elif event.key == pygame.K_PAGEUP:
                        agent_offset = max(0, agent_offset - 1)

            if not paused and not help_open and not sim.game_over:
                accumulator += dt
                interval = 1.0 / ticks_per_second
                while accumulator >= interval and not sim.game_over:
                    sim.step()
                    accumulator -= interval

            if export:
                export.poll()
                reports_closed = reports.closed
            if reports and not reports_closed and not sim.game_over:
                reports.update()

            if sim.game_over:
                paused, accumulator = True, 0.0
                if not game_over_handled:
                    game_over_handled = True
                    if hasattr(sim.policy, "close"):
                        sim.policy.close()
                    reason = "no survivors remain" if not any(agent.alive for agent in sim.agents) else "tick limit reached"
                    print(f"Game over at tick {sim.tick}: {reason}.", flush=True)
                    if reports:
                        reports.save()
                if reports and not reports_closed and any(agent.alive for agent in sim.agents) and all(future.done() for future in reports.futures):
                    reports.close()  # Finalize once background postmortems finish, without freezing the UI.
                    reports_closed = True

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

            for plot in sim.homestead.plots.values():
                x, y = plot.position
                pygame.draw.rect(screen, (180, 225, 70) if plot.progress >= 160 else (70, 160, 80),
                                 (x * tile_size + 3, y * tile_size + 3, tile_size - 6, tile_size - 6), 2)
            for fire in sim.homestead.fires.values():
                x, y = fire.position
                pygame.draw.circle(screen, (255, 130, 25), (x * tile_size + tile_size // 2, y * tile_size + tile_size // 2), 4)
            for position, reservoir in sim.homestead.reservoirs.items():
                x, y = position
                label = small.render(str(reservoir.water), True, (120, 225, 255))
                screen.blit(label, (x * tile_size + 1, y * tile_size))

            for animal in sim.wildlife.animals:
                if animal.alive:
                    x, y = animal.position
                    center = (x * tile_size + tile_size // 2, y * tile_size + tile_size // 2)
                    color = {"deer": (195, 151, 93), "boar": (100, 100, 110), "rabbit": (235, 235, 225)}[animal.kind]
                    pygame.draw.circle(screen, color, center, 6)
                    label = small.render({"deer": "D", "boar": "b", "rabbit": "r"}[animal.kind], True, (15, 15, 15))
                    screen.blit(label, label.get_rect(center=center))

            for predator in sim.predators:
                if predator.alive:
                    px, py = predator.position
                    rect = pygame.Rect(px * tile_size + 3, py * tile_size + 3, tile_size - 6, tile_size - 6)
                    pygame.draw.rect(screen, (205, 50, 45) if predator.kind == "wolf" else (100, 49, 27), rect)
                    label = small.render("W" if predator.kind == "wolf" else "B", True, (255, 255, 255))
                    screen.blit(label, label.get_rect(center=rect.center))

            for position in sim.social.active_sites:
                x, y = position
                pygame.draw.rect(screen, (235, 195, 100),
                                 (x * tile_size + 3, y * tile_size + 3, tile_size - 6, tile_size - 6), 2)

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
            speed = "Game over" if sim.game_over else "Help (paused)" if help_open else "Paused" if paused else f"Speed {ticks_per_second} ticks/s"
            population = sum(agent.alive for agent in sim.agents)
            screen.blit(small.render(f"{speed} · Alive {population}/{len(sim.agents)} · Predators {living_predators} · Scroll", True, (150, 155, 160)), (left, 31))
            if model:
                summary = f"LLM {sim.policy.accepted} plans / {sim.policy.cache_hits} cached / {sim.policy.failed} failed"
                screen.blit(small.render(summary, True, (190, 210, 230)), (left, 61))
            conditions = sim.conditions
            weather = "thunderstorm" if conditions.thunderstorm else "very hot sunshine" if conditions.heatwave else conditions.weather
            climate_line = f"{conditions.season.title()} · {weather} · {conditions.temperature:g}°C"
            screen.blit(small.render(climate_line, True, (170, 205, 230)), (left, 45))
            y = 80 if model else 66
            for index, agent in enumerate(sim.agents[agent_offset:agent_offset + 5], start=agent_offset):
                color = AGENT_COLORS[index % len(AGENT_COLORS)]
                screen.blit(font.render(f"{agent.name} — {sim.active_objective(agent)}", True, color), (left, y))
                y += 18
                needs = f"HP {agent.health:3.0f}  hunger {agent.hunger:3.0f}  thirst {agent.thirst:3.0f}  energy {agent.energy:3.0f}"
                screen.blit(small.render(needs, True, (210, 210, 210)), (left, y))
                y += 16
                inventory = ", ".join(f"{name}:{amount}" for name, amount in agent.inventory.items() if amount) or "empty"
                inventory_line = f"INT {agent.intelligence} · inventory: {inventory}"
                if small.size(inventory_line)[0] > sidebar - 28:
                    while inventory_line and small.size(inventory_line + "…")[0] > sidebar - 28:
                        inventory_line = inventory_line[:-1]
                    inventory_line += "…"
                screen.blit(small.render(inventory_line, True, (180, 185, 190)), (left, y))
                y += 16
                plan = (agent.current_plan if agent.suspended_plan_key else
                        getattr(sim.policy, "model_plans", {}).get(agent.id, agent.current_plan))
                if agent.social_project and not agent.suspended_plan_key:
                    plan = sim.social.project_plans.get((agent.social_project, agent.id), plan)
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

            mx, my = pygame.mouse.get_pos()
            hovered = next((predator for predator in sim.predators if predator.alive
                            and predator.position == (mx // tile_size, my // tile_size)), None)
            if hovered:
                details = [f"{hovered.id} ({hovered.kind}) · {hovered.behavior}",
                           f"HP {hovered.health:.0f}  hunger {hovered.hunger:.0f}  thirst {hovered.thirst:.0f}"]
            else:
                survivor = next((agent for agent in sim.agents if agent.alive
                                 and agent.position == (mx // tile_size, my // tile_size)), None)
                details = []
                if survivor:
                    traits = survivor.social_traits
                    details = [f"{survivor.name}: generosity {traits.generosity}, cooperation {traits.cooperation}, caution {traits.caution}",
                               f"Project: {survivor.social_project or 'none'} · Offers: {len(sim.social.pending(survivor))}"]
                elif 0 <= mx // tile_size < sim.world.size and 0 <= my // tile_size < sim.world.size:
                    tile = sim.world.tile((mx // tile_size, my // tile_size))
                    if tile.shelter_owner:
                        occupied = sum(agent.alive and bool(agent.sleep_remaining)
                                       and agent.position == (mx // tile_size, my // tile_size) for agent in sim.agents)
                        details = [f"Shelter: {tile.shelter_capacity} bed(s), {occupied} sleeping",
                                   "Residents: " + ", ".join(sorted(tile.shelter_residents or {tile.shelter_owner}))]
                        reservoir = sim.homestead.reservoirs.get((mx // tile_size, my // tile_size))
                        if reservoir:
                            details[-1] += f" · Water: {reservoir.water}/{reservoir.capacity}"
                    elif (mx // tile_size, my // tile_size) in sim.homestead.plots:
                        plot = sim.homestead.plots[(mx // tile_size, my // tile_size)]
                        details = [f"Vegetable plot: {plot.progress:g}/160 growth", f"Home: {plot.home}"]
                    elif (mx // tile_size, my // tile_size) in sim.homestead.fires:
                        fire = sim.homestead.fires[(mx // tile_size, my // tile_size)]
                        details = [f"Fire fuel: {fire.fuel} ticks", "Warmth: +8°C within one tile"]
            if details:
                labels = [small.render(line, True, (240, 240, 240)) for line in details]
                width = max(label.get_width() for label in labels) + 12
                tx = max(0, min(mx + 12, screen.get_width() - width))
                ty = max(0, min(my + 12, screen.get_height() - 44))
                pygame.draw.rect(screen, (25, 28, 32), (tx, ty, width, 44))
                for index, label in enumerate(labels):
                    screen.blit(label, (tx + 6, ty + 5 + index * 17))

            if sim.game_over and not any(agent.alive for agent in sim.agents) and export:
                import textwrap
                overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 175))
                screen.blit(overlay, (0, 0))
                pygame.draw.rect(screen, (30, 35, 43), dialog, border_radius=12)
                pygame.draw.rect(screen, (110, 120, 135), dialog, 1, border_radius=12)
                screen.blit(font.render("Game Over", True, (245, 245, 245)), (dialog.left + 30, dialog.top + 24))
                screen.blit(font.render(f"All survivors died. Final tick: {sim.tick}.", True, (220, 220, 220)), (dialog.left + 30, dialog.top + 54))
                for index, line in enumerate(textwrap.wrap(export.message, 75)[:4]):
                    screen.blit(small.render(line, True, (185, 195, 210)), (dialog.left + 30, dialog.top + 87 + index * 18))
                label = ("Generating…" if export.busy else "Open postmortem" if export.path else
                         "Retry generation" if export.error else "Generate postmortem")
                pygame.draw.rect(screen, (60, 85, 115) if export.busy else (45, 115, 165), generate_button, border_radius=6)
                screen.blit(font.render(label, True, (255, 255, 255)), (generate_button.left + 14, generate_button.top + 11))
                pygame.draw.rect(screen, (65, 70, 80), exit_button, border_radius=6)
                screen.blit(font.render("Exit", True, (255, 255, 255)), (exit_button.left + 14, exit_button.top + 11))

            if help_open:
                overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
                overlay.fill((0, 0, 0, 185))
                screen.blit(overlay, (0, 0))
                pygame.draw.rect(screen, (30, 35, 43), help_dialog, border_radius=12)
                pygame.draw.rect(screen, (110, 120, 135), help_dialog, 1, border_radius=12)
                screen.blit(font.render("Keyboard shortcuts", True, (245, 245, 245)), (help_dialog.left + 28, help_dialog.top + 24))
                for index, (key, description) in enumerate(HELP_SHORTCUTS):
                    row_y = help_dialog.top + 65 + index * 25
                    screen.blit(small.render(key, True, (115, 195, 250)), (help_dialog.left + 28, row_y))
                    screen.blit(small.render(description, True, (225, 230, 235)), (help_dialog.left + 180, row_y))
                screen.blit(small.render("Simulation pauses here; closing help restores its prior state.", True, (170, 180, 190)), (help_dialog.left + 28, help_dialog.bottom - 95))
                pygame.draw.rect(screen, (45, 115, 165), help_close, border_radius=6)
                screen.blit(font.render("Close", True, (255, 255, 255)), (help_close.left + 14, help_close.top + 9))

            pygame.display.flip()
            if screenshot:
                pygame.image.save(screen, screenshot)
                running = False
    except KeyboardInterrupt:
        pass
    finally:
        pygame.quit()
        if export:
            export.finish()
        if not game_over_handled and hasattr(sim.policy, "close"):
            sim.policy.close()
        if reports and not reports.closed:
            reports.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Survival Island")
    parser.add_argument("--model", help="Ollama model name; omit for reference policy")
    parser.add_argument("--team", help="Team JSON5 file")
    parser.add_argument("--competition", help="Competition JSON file")
    parser.add_argument("--seed", type=int, help="Competition seed to watch (default: first)")
    parser.add_argument("--starting-season", choices=("spring", "summer", "autumn", "winter"),
                        help="Starting season for normal play (default: spring); competitions use their JSON")
    parser.add_argument("--endpoint", default="http://localhost:11434/api/chat")
    parser.add_argument("--screenshot", help="Save a rendered frame after 120 reference-policy ticks and exit")
    parser.add_argument("--report-dir", help="Directory for saved survivor histories (default: unique survival-runs subdirectory)")
    parser.add_argument("--no-postmortem", action="store_true", help="Save traces without generating model death summaries")
    parser.add_argument("--inference-cooldown", type=float, default=3.0,
                        help="Minimum idle seconds between model calls (default: 3)")
    args = parser.parse_args()
    run(model=args.model, endpoint=args.endpoint, screenshot=args.screenshot,
        report_dir=args.report_dir, postmortems=not args.no_postmortem,
        inference_cooldown=args.inference_cooldown, team=args.team,
        competition=args.competition, seed=args.seed, starting_season=args.starting_season)
