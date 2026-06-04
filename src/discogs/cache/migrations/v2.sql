-- v2: persist per-recommendation sub-scores so `discogs explain` can show the
-- score breakdown after the fact (the pipeline computes them but previously
-- only the total `score` survived the run).
ALTER TABLE recommendation_history ADD COLUMN subscores_json TEXT;
