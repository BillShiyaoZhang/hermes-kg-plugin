import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
} from "d3-force";
import type { SimulationNodeDatum, SimulationLinkDatum } from "d3-force";
import type { GraphEdge, GraphNode } from "./graphModel";
import type { GraphPoint, GraphPositions } from "./graphLayout";

interface Particle extends SimulationNodeDatum {
  id: string;
  x: number;
  y: number;
  anchor: GraphPoint;
}
interface Spring extends SimulationLinkDatum<Particle> {
  source: string | Particle;
  target: string | Particle;
  weight: number;
  distance: number;
}
interface Topology {
  ids: string[];
  neighbors: Map<string, Map<string, number>>;
  springs: Spring[];
}

/** Geometry only: never returns, changes or stores knowledge edges. */
function topology(nodes: GraphNode[], edges: GraphEdge[]): Topology {
  const ids = [...new Set(nodes.map((node) => node.id))].sort();
  const neighbors = new Map(ids.map((id) => [id, new Map<string, number>()]));
  for (const edge of edges) {
    if (
      edge.source === edge.target ||
      !neighbors.has(edge.source) ||
      !neighbors.has(edge.target)
    )
      continue;
    const weight = Math.min(4, Math.max(1, edge.count ?? 1));
    const add = (a: string, b: string) =>
      neighbors.get(a)!.set(b, (neighbors.get(a)!.get(b) ?? 0) + weight);
    add(edge.source, edge.target);
    add(edge.target, edge.source);
  }
  const springs: Spring[] = [];
  for (const source of ids) {
    const a = neighbors.get(source)!;
    for (const [target, count] of [...a].sort(([x], [y]) =>
      x.localeCompare(y),
    )) {
      if (source >= target) continue;
      const b = neighbors.get(target)!;
      // Shared high-degree neighbors carry less information than a close circle.
      let shared = 0;
      for (const id of a.keys())
        if (b.has(id)) shared += 1 / Math.max(1, neighbors.get(id)!.size - 1);
      const similarity = shared / Math.sqrt(a.size * b.size);
      const weight =
        ((1 + Math.log2(count)) * (1 + 2 * similarity)) /
        Math.sqrt(a.size * b.size);
      a.set(target, weight);
      b.set(source, weight);
      springs.push({
        source,
        target,
        weight,
        distance: 185 + 30 * Math.log2(Math.max(a.size, b.size)),
      });
    }
  }
  return { ids, neighbors, springs };
}

/** Deterministic, bounded modularity moves on degree-normalized real links. */
function communities(graph: Topology): Map<string, string> {
  const labels = new Map(graph.ids.map((id) => [id, id]));
  const degree = new Map(
    graph.ids.map((id) => [
      id,
      [...graph.neighbors.get(id)!.values()].reduce((a, b) => a + b, 0),
    ]),
  );
  const total = [...degree.values()].reduce((a, b) => a + b, 0) || 1;
  const totals = new Map(degree);
  for (let pass = 0; pass < 12; pass++) {
    let changed = false;
    for (const id of graph.ids) {
      const current = labels.get(id)!,
        k = degree.get(id)!;
      totals.set(current, (totals.get(current) ?? 0) - k);
      const support = new Map<string, number>();
      for (const [other, weight] of graph.neighbors.get(id)!) {
        const label = labels.get(other)!;
        support.set(label, (support.get(label) ?? 0) + weight);
      }
      const gain = (label: string) =>
        (support.get(label) ?? 0) -
        (1.15 * k * (totals.get(label) ?? 0)) / total;
      let best = current,
        bestGain = gain(current);
      for (const label of [...support.keys()].sort()) {
        const candidate = gain(label);
        if (candidate > bestGain + 1e-8) {
          best = label;
          bestGain = candidate;
        }
      }
      labels.set(id, best);
      totals.set(best, (totals.get(best) ?? 0) + k);
      changed ||= best !== current;
    }
    if (!changed) break;
  }
  return labels;
}

function simulation(particles: Particle[], springs: Spring[], local: boolean) {
  // d3 mutates particles and spring endpoints, so all inputs are private copies.
  return forceSimulation(particles)
    .stop()
    .alphaDecay(local ? 0.07 : 0.035)
    .velocityDecay(0.48)
    .force(
      "links",
      forceLink<Particle, Spring>(springs.map((spring) => ({ ...spring })))
        .id((node) => node.id)
        .distance((spring) => spring.distance)
        .strength((spring) => Math.min(0.55, 0.08 + spring.weight * 0.45)),
    )
    .force(
      "charge",
      forceManyBody<Particle>()
        .strength(-850)
        .distanceMin(50)
        .distanceMax(1100),
    )
    .force("collision", forceCollide<Particle>(92).strength(0.9).iterations(2))
    .force(
      "x",
      forceX<Particle>((node) => node.anchor.x).strength(local ? 0.025 : 0.035),
    )
    .force(
      "y",
      forceY<Particle>((node) => node.anchor.y).strength(local ? 0.025 : 0.035),
    );
}

const snapshot = (particles: Particle[]): GraphPositions =>
  Object.fromEntries(
    particles.map((node) => [node.id, { x: node.x, y: node.y }]),
  );

export async function relationGraphLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
  signal?: AbortSignal,
  constraints?: { positions: GraphPositions; pinnedIds: string[] },
): Promise<GraphPositions> {
  const graph = topology(nodes, edges),
    labels = communities(graph);
  if (!graph.ids.length) return {};
  const groups = new Map<string, string[]>();
  for (const id of graph.ids) {
    const label = labels.get(id)!;
    groups.set(label, [...(groups.get(label) ?? []), id]);
  }
  const sorted = [...groups.values()].sort(
    (a, b) => b.length - a.length || a[0].localeCompare(b[0]),
  );
  const columns = Math.max(1, Math.ceil(Math.sqrt(sorted.length)));
  const pitch = Math.max(
    650,
    ...sorted.map((group) => Math.sqrt(group.length) * 245 + 280),
  );
  const particles: Particle[] = [];
  sorted.forEach((group, index) => {
    const anchor = {
      x: (index % columns) * pitch,
      y: Math.floor(index / columns) * pitch,
    };
    group.forEach((id, offset) => {
      const angle = offset * Math.PI * (3 - Math.sqrt(5)),
        radius = Math.sqrt(offset) * 140;
      particles.push({
        id,
        x: anchor.x + Math.cos(angle) * radius,
        y: anchor.y + Math.sin(angle) * radius,
        anchor,
      });
    });
  });
  particles.sort((a, b) => a.id.localeCompare(b.id));
  if (constraints && particles.length) {
    const existing = particles
      .map((particle) => constraints.positions[particle.id])
      .filter((point): point is GraphPoint => !!point);
    if (existing.length) {
      const dx =
        existing.reduce((sum, point) => sum + point.x, 0) / existing.length -
        particles.reduce((sum, point) => sum + point.x, 0) / particles.length;
      const dy =
        existing.reduce((sum, point) => sum + point.y, 0) / existing.length -
        particles.reduce((sum, point) => sum + point.y, 0) / particles.length;
      const pins = new Set(constraints.pinnedIds);
      for (const particle of particles) {
        particle.x += dx;
        particle.y += dy;
        particle.anchor = {
          x: particle.anchor.x + dx,
          y: particle.anchor.y + dy,
        };
        if (pins.has(particle.id) && constraints.positions[particle.id]) {
          particle.fx = particle.x = constraints.positions[particle.id].x;
          particle.fy = particle.y = constraints.positions[particle.id].y;
        }
      }
    }
  }
  const engine = simulation(particles, graph.springs, false);
  // Yield between bounded chunks; large initial layouts do not monopolize input.
  for (let tick = 0; tick < 180; tick += 15) {
    if (signal?.aborted) break;
    engine.tick(15);
    if (tick < 165)
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
  }
  engine.stop();
  return snapshot(particles);
}

/** Only a bounded related neighborhood can move; every other node is an anchor. */
export function createGraphDragSimulation(
  nodes: GraphNode[],
  edges: GraphEdge[],
  positions: GraphPositions,
  draggedIds: string[],
  pinnedIds: string[],
) {
  const graph = topology(nodes, edges),
    pins = new Set(pinnedIds),
    dragged = new Set(draggedIds.filter((id) => !pins.has(id)));
  const active = new Set(dragged),
    frontier = [...dragged];
  for (let hop = 0; hop < 2; hop++) {
    const candidates = new Map<string, number>();
    for (const id of frontier.splice(0)) {
      const neighbors = graph.neighbors.get(id);
      // Do not propagate through a ubiquitous hub into unrelated neighborhoods.
      if (
        !dragged.has(id) &&
        (neighbors?.size ?? 0) > Math.max(6, Math.sqrt(graph.ids.length))
      )
        continue;
      for (const [other, weight] of neighbors ?? [])
        if (!active.has(other) && !pins.has(other))
          candidates.set(other, Math.max(candidates.get(other) ?? 0, weight));
    }
    for (const [id] of [...candidates]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, Math.max(0, 48 - active.size))) {
      active.add(id);
      frontier.push(id);
    }
  }
  const particles: Particle[] = graph.ids
    .filter((id) => positions[id])
    .map((id) => ({
      id,
      ...positions[id],
      anchor: { ...positions[id] },
      ...(!active.has(id) || dragged.has(id) || pins.has(id)
        ? { fx: positions[id].x, fy: positions[id].y }
        : {}),
    }));
  const ids = new Set(particles.map((node) => node.id));
  const engine = simulation(
    particles,
    graph.springs.filter(
      (spring) =>
        ids.has(spring.source as string) && ids.has(spring.target as string),
    ),
    true,
  );
  engine.alpha(0.55);
  let ticks = 0;
  return {
    move(targets: GraphPositions) {
      for (const particle of particles)
        if (dragged.has(particle.id) && targets[particle.id]) {
          particle.fx = particle.x = targets[particle.id].x;
          particle.fy = particle.y = targets[particle.id].y;
        }
      engine.alpha(0.55);
      ticks = 0;
    },
    tick(count = 2) {
      engine.tick(count);
      ticks += count;
      return snapshot(particles);
    },
    get done() {
      return ticks >= 84 || engine.alpha() < 0.025;
    },
    stop() {
      engine.stop();
    },
  };
}
export type GraphDragSimulation = ReturnType<typeof createGraphDragSimulation>;
