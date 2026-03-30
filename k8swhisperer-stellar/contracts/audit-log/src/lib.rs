#![no_std]
use soroban_sdk::{
    contract, contractimpl, contracttype, symbol_short,
    vec, Env, String, Vec,
};

/// A single audit event stored on-chain.
#[contracttype]
#[derive(Clone)]
pub struct AuditEvent {
    /// e.g. "HITL_DECISION", "AUTO_EXECUTE", "HUMAN_ALERT"
    pub event_type: String,
    /// The Kubernetes pod name involved
    pub pod: String,
    /// Short description / action taken
    pub detail: String,
    /// Unix timestamp (seconds) — caller provides this
    pub timestamp: u64,
}

const EVENTS_KEY: soroban_sdk::Symbol = symbol_short!("EVENTS");

#[contract]
pub struct AuditLogContract;

#[contractimpl]
impl AuditLogContract {
    /// Append a new audit event to the on-chain log.
    pub fn log_event(
        env: Env,
        event_type: String,
        pod: String,
        detail: String,
        timestamp: u64,
    ) {
        let mut events: Vec<AuditEvent> = env
            .storage()
            .instance()
            .get(&EVENTS_KEY)
            .unwrap_or(vec![&env]);

        events.push_back(AuditEvent {
            event_type,
            pod,
            detail,
            timestamp,
        });

        env.storage().instance().set(&EVENTS_KEY, &events);
        // Keep storage alive
        env.storage().instance().extend_ttl(100, 100);
    }

    /// Return all stored audit events.
    pub fn get_events(env: Env) -> Vec<AuditEvent> {
        env.storage()
            .instance()
            .get(&EVENTS_KEY)
            .unwrap_or(vec![&env])
    }

    /// Return the total count of logged events.
    pub fn event_count(env: Env) -> u32 {
        let events: Vec<AuditEvent> = env
            .storage()
            .instance()
            .get(&EVENTS_KEY)
            .unwrap_or(vec![&env]);
        events.len()
    }
}

#[cfg(test)]
mod test {
    use super::*;
    use soroban_sdk::{testutils::Address as _, Env, String};

    #[test]
    fn test_log_and_retrieve() {
        let env = Env::default();
        let contract_id = env.register_contract(None, AuditLogContract);
        let client = AuditLogContractClient::new(&env, &contract_id);

        client.log_event(
            &String::from_str(&env, "HITL_DECISION"),
            &String::from_str(&env, "crash-loop-pod-abc"),
            &String::from_str(&env, "approved: delete pod"),
            &1_700_000_000u64,
        );

        assert_eq!(client.event_count(), 1);
        let events = client.get_events();
        assert_eq!(events.len(), 1);
    }
}
