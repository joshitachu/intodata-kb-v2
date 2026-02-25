export interface Project {
  id: string;
  display_name: string;
  repo_url: string | null;
  local_path: string;
  platform: string;
  source_type: string;
  status: string;
  last_scan_at: string | null;
  last_generate_at: string | null;
  last_rag_push_at: string | null;
  job_count: number;
  doc_count: number;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ProjectListResponse {
  projects: Project[];
  total: number;
}

export interface StatusResponse {
  status: string;
  message: string;
}

export interface LogEvent {
  project_id: string;
  level: string;
  message: string;
}

export interface GenerateRun {
  id: number;
  project_id: string;
  triggered_by: string;
  status: string;
  started_at: string;
  completed_at: string | null;
  cost_usd: number | null;
  duration_ms: number | null;
  duration_api_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  num_turns: number | null;
  session_id: string | null;
  doc_count: number;
  error_message: string | null;
  log_file: string | null;
}

export interface LogFile {
  filename: string;
  project_id: string;
  timestamp: string;
  size_bytes: number;
}
