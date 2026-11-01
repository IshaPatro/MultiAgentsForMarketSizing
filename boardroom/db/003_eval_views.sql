-- SQLite evaluation dashboard; applied by debate eval-report.
CREATE VIEW IF NOT EXISTS eval_run_metrics AS
SELECT r.run_id, r.company_id, r.status,
       json_extract(r.config_json, '$.dry_run') AS dry_run,
       json_extract(r.config_json, '$.profile') AS profile,
       COALESCE(json_extract(r.config_json, '$.with_rag'),0) AS with_rag,
       COALESCE(json_extract(r.config_json, '$.no_rag'),0) AS no_rag,
       json_extract(r.config_json, '$.prompt_version') AS prompt_version,
       json_extract(r.config_json, '$.limits.max_rounds') AS max_rounds,
       r.total_cost_usd,
       json_extract(d.decision_json, '$.decision') AS decision,
       (SELECT COUNT(*) FROM turns t WHERE t.run_id=r.run_id) AS turns,
       (SELECT COUNT(*) FROM turns t WHERE t.run_id=r.run_id
        AND t.word_count >
            COALESCE(json_extract(r.config_json,'$.limits.max_words_per_turn'),60)) AS over_word_limit,
       (SELECT COUNT(*) FROM retrievals q WHERE q.run_id=r.run_id) AS retrievals,
       (SELECT COUNT(*) FROM events e WHERE e.run_id=r.run_id AND e.type='leak_attempt') AS memory_leak_attempts,
       (SELECT COUNT(*) FROM claims c WHERE c.run_id=r.run_id AND c.type IN ('fact','number')) AS factual_claims,
       (SELECT COUNT(*) FROM claims c WHERE c.run_id=r.run_id AND c.type IN ('fact','number')
        AND c.verdict IN ('supported','contradicted')
        AND json_array_length(c.evidence_ids)>0
        AND NOT EXISTS (
          SELECT 1 FROM json_each(c.evidence_ids) cited
          WHERE NOT EXISTS (
            SELECT 1 FROM retrievals q, json_each(q.chunk_ids) found
            WHERE q.run_id=c.run_id AND q.turn_seq=c.turn_seq AND found.value=cited.value
          )
        )) AS cited_factual_claims,
       (SELECT COUNT(*) FROM claims c WHERE c.run_id=r.run_id AND c.verdict='miscalculated') AS miscalculations,
       (SELECT COUNT(*) FROM turns t WHERE t.run_id=r.run_id AND t.invalid_citations>0) AS turns_with_invalid_citations,
       (SELECT COUNT(*) FROM model_calls m WHERE m.run_id=r.run_id) AS model_calls,
       (SELECT COALESCE(SUM(m.cost_usd),0) FROM model_calls m WHERE m.run_id=r.run_id) AS model_cost_usd
FROM runs r LEFT JOIN decisions d ON d.run_id=r.run_id;

CREATE VIEW IF NOT EXISTS eval_decision_stability AS
SELECT company_id, profile, with_rag, no_rag, prompt_version, max_rounds, dry_run,
       COUNT(*) AS runs,
       SUM(decision='invest') AS invest,
       SUM(decision='pass') AS pass,
       SUM(decision='invest_with_conditions') AS conditional
FROM eval_run_metrics WHERE status='completed' AND decision IS NOT NULL
GROUP BY company_id, profile, with_rag, no_rag, prompt_version, max_rounds, dry_run;
