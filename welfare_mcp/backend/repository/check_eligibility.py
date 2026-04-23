def check_eligibility_query() -> str:
    return """
        SELECT * FROM (
            SELECT
                ws.service_id,
                ws.service_name,
                ws.service_purpose,
                ws.apply_url,

                (1 - (ws.embedding <=> %s::vector))::float AS vector_score,

                (CASE
                    WHEN ws.service_name    LIKE ANY(%s)
                    OR   ws.service_purpose LIKE ANY(%s)
                    THEN 0.5
                    ELSE 0
                END)::float AS intent_bonus,

                (
                    -- 지역 보너스
                    (CASE
                        WHEN wt.sido IS NULL OR wt.sido = '' THEN 0.1
                        WHEN wt.sido ILIKE %s               THEN 0.2
                        ELSE 0
                    END)
                    +
                    -- 성별 보너스
                    (CASE
                        WHEN wt.gender = 'A'  THEN 0.05
                        WHEN wt.gender = %s   THEN 0.1
                        ELSE 0
                    END)
                    +
                    -- 가구 형태 보너스
                    (CASE
                        WHEN %s IS NOT NULL
                         AND wt.household_types @> ARRAY[%s]::TEXT[]
                        THEN 0.15
                        ELSE 0
                    END)
                    +
                    -- 소득 기준 보너스
                    (CASE
                        WHEN %s IS NOT NULL
                         AND wc.income_min_pct <= %s
                         AND wc.income_max_pct >= %s
                        THEN 0.15
                        ELSE 0
                    END)
                )::float AS profile_bonus

            FROM welfare_service   ws
            JOIN welfare_target    wt ON wt.service_id = ws.service_id
            JOIN welfare_criteria  wc ON wc.service_id = ws.service_id

            WHERE
                wt.min_age <= %s
                AND (wt.max_age = 0 OR wt.max_age >= %s)

        ) sub

        ORDER BY (vector_score + intent_bonus + profile_bonus) DESC
        LIMIT 5
        """
    
