/**
 * Client-side mirror of the backend's profile checks
 * (backend/api/schemas.py: check_local_dir, check_remote_dir and the
 * ge=1 bounds), so the form can explain a problem before sending it.
 */

/** Same as the backend's REMOTE_PATH_PATTERN: "<remote>:<path>". */
export const REMOTE_PATH_PATTERN = /^[A-Za-z0-9_][A-Za-z0-9_-]*:/;

export interface ProfileFormValues {
  name:                  string;
  local_dir:             string;
  remote_dir:            string;
  debounce_seconds:      string;
  pull_interval_minutes: string;
  max_retries:           string;
}

export type ProfileField = keyof ProfileFormValues;

/** Field → i18n key of the problem. Empty when the values are valid. */
export type ProfileFormErrors = Partial<Record<ProfileField, string>>;

const CONTROL_CHARS = /[\r\n\0]/;

function positiveInteger (value: string): boolean {
  return /^\d+$/.test(value.trim()) && Number(value) >= 1;
}

export function validateProfileForm (values: ProfileFormValues): ProfileFormErrors {
  const errors: ProfileFormErrors = {};

  if (!values.name.trim()) errors.name = 'profiles.validation.nameRequired';

  if (!values.local_dir.startsWith('/') || CONTROL_CHARS.test(values.local_dir)) {
    errors.local_dir = 'profiles.validation.localDirAbsolute';
  }

  if (!REMOTE_PATH_PATTERN.test(values.remote_dir) || CONTROL_CHARS.test(values.remote_dir)) {
    errors.remote_dir = 'profiles.validation.remoteDirFormat';
  }

  if (!positiveInteger(values.debounce_seconds)) errors.debounce_seconds = 'profiles.validation.debounceMin';
  if (!positiveInteger(values.pull_interval_minutes)) errors.pull_interval_minutes = 'profiles.validation.pullIntervalMin';
  if (!positiveInteger(values.max_retries)) errors.max_retries = 'profiles.validation.maxRetriesMin';

  return errors;
}
