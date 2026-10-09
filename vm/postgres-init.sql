CREATE TABLE state (key text PRIMARY KEY, value text NOT NULL);
INSERT INTO state VALUES ('counter', '0'), ('privileged', 'unchanged'),
                         ('canary', 'VAULT_SYNTHETIC_CANARY_f6b2');
CREATE TABLE audit_events (
    id bigserial PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    action text NOT NULL,
    marker text NOT NULL,
    outcome text NOT NULL
);
