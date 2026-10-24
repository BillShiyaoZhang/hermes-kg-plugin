export type JsonRecord = Record<string, unknown>;
export type BuiltinEntityType =
  | "person"
  | "event"
  | "decision"
  | "preference"
  | "location"
  | "concept"
  | "unknown";
/** The server may register namespace-scoped entity types beyond the builtins. */
export type EntityType = BuiltinEntityType | (string & {});
export type MemoryState = "active" | "archived";
export type Version = number | string;

export interface Evidence {
  messageId?: string | number;
  quote: string;
  sessionId?: string;
  origin?: string;
  timestamp?: string;
  kind?: string;
  changes?: JsonRecord;
  reason?: string;
}

export interface Revision {
  id: string;
  action: string;
  reason?: string;
  createdAt?: string;
  version?: Version;
  changes?: JsonRecord;
  state: JsonRecord;
}

export interface Memory {
  uri: string;
  type: EntityType;
  name: string;
  description: string;
  properties: JsonRecord;
  version?: Version;
  status: MemoryState;
  readOnly: boolean;
  semanticStatus?: string;
  updatedAt?: string;
  createdAt?: string;
  eventTime?: string;
  endTime?: string;
  timePrecision?: string;
  participants: string[];
  evidence: Evidence[];
  relations: JsonRecord[];
  revisions: Revision[];
}

export interface MemoryPage {
  items: Memory[];
  total: number;
  offset: number;
  limit: number;
  mode?: string;
  warnings: string[];
  truncated: boolean;
}
export interface Review {
  id: string;
  reason?: string;
  status: string;
  createdAt?: string;
  entities: Memory[];
  relations: JsonRecord[];
  expectedVersions?: Record<string, Version>;
}
export interface ReviewPage {
  items: Review[];
  total: number;
  total_pending: number;
  offset: number;
  limit: number;
  has_more: boolean;
}
export interface SyncJob {
  id: string;
  status: string;
  createdAt?: string;
  startedAt?: string;
  finishedAt?: string;
  kind?: string;
  error?: string;
  summary?: string;
}
export interface SessionSummary {
  id: string;
  source: string;
  startedAt?: string;
  messageCount: number | null;
}
export interface ConsoleStatus {
  connected: boolean | null;
  totalMemories: number | null;
  counts: Record<string, number>;
  paused: boolean | null;
  pendingCount: number | null;
  queuedCount: number | null;
  failedCount: number | null;
  schedulerEnabled: boolean | null;
  sourceAvailable: boolean | null;
  syncAvailable: boolean | null;
  lastRun?: string;
  jobs: SyncJob[];
  search: {
    mode?: string;
    indexed: number | null;
    pending: number | null;
    modelReady: boolean | null;
    ready: boolean | null;
    indexes: JsonRecord;
  };
  compatibility: JsonRecord;
  raw: JsonRecord;
}
export interface MutationReceipt extends JsonRecord {
  ok?: boolean;
  receipt?: unknown;
}
