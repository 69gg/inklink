export type ContinuationMode = 'fixed' | 'to_ending';

export interface RunForm {
  input_dir: string;
  config_path: string;
  log_root: string;
  runtime_id: string;
  output_mode: 'output' | 'writeback';
  continuation_mode: ContinuationMode;
  chapter_count: number;
  ending_min_chapters: number;
  ending_max_chapters: number;
  start_chapter: string;
  min_chars: number;
  max_chars: number;
  max_revision_rounds: string;
  auto_approve: boolean;
  notes: string;
  notes_file: string;
}

export interface PhaseStatus {
  key: string;
  label: string;
  total: number;
  completed: number;
  running: number;
  waiting: number;
  failed: number;
  current: boolean;
}

export interface RuntimeArtifact {
  artifact_id: string;
  artifact_type: string;
  version: number;
  is_approved: boolean;
  is_draft: boolean;
  is_invalidated: boolean;
  approval_id: string | null;
  created_at?: string;
}

export interface RuntimeApproval {
  approval_id: string;
  approval_type: string;
  artifact_id: string | null;
  artifact_version: number | null;
  status: string;
}

export interface RuntimeEvent {
  timestamp?: string;
  event_type?: string;
  payload?: Record<string, unknown>;
}

export interface RuntimeSnapshot {
  runtime_id: string | null;
  status: string;
  input_dir: string | null;
  log_dir: string | null;
  phase_statuses: PhaseStatus[];
  artifacts: RuntimeArtifact[];
  approvals: RuntimeApproval[];
  messages: Record<string, unknown>[];
  events: RuntimeEvent[];
  usage: Record<string, unknown>[];
  run_summary: Record<string, unknown>;
  waiting_approval: RuntimeApproval | null;
  waiting_approval_id: string | null;
  current_node: Record<string, unknown> | null;
  current_phase_label: string;
  latest_message: string;
  stale_hint: string | null;
  pipeline_running: boolean;
  state_error: string | null;
  error: string | null;
}

export interface ArtifactPayload {
  artifact_id: string;
  artifact_type: string;
  version: number;
  payload: unknown;
  is_approved: boolean;
  is_draft: boolean;
  is_invalidated: boolean;
}

export interface ArtifactDiff {
  artifact_id: string;
  left_version: number;
  right_version: number;
  diff: string;
}
