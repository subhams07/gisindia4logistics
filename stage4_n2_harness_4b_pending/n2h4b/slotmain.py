# ---- slot mains H0/H1/H2/H3 (run inside the contained child, or in-process by the tests) ----------------------
import csv


def _write_json(path, obj):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, json.dumps(obj, sort_keys=True, default=str).encode("ascii"))
    finally:
        os.close(fd)


def _count_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1


def h3_enter_models(record, out_dir, district_source, population_source, ice, phm, pio, call=None):
    """Two-domain validation FIRST (repeated at model entry), then the exact real H3 calls."""
    call = call or (lambda name, fn, *a, **k: fn(*a, **k))
    troot = os.path.dirname(os.path.dirname(record["files"]["summary"]["dest"]))
    validate_transfer(record, troot, district_source=district_source, population_source=population_source,
                      sample_rows=record["sample_rows"], require_positions=True)
    ctx_json = load_json(record["files"]["context"]["dest"])
    os.makedirs(out_dir, exist_ok=True)
    plan = h3_call_plan(record, str(out_dir))
    with FullContextPopulation(pio, ctx_json, record["sample_positions"],
                               population_rows_expected=record["population"]["rows"]) as fcp:
        call("compute_modal_costs", ice.compute_modal_costs, ice.FreightCostParameters(),
             **plan["compute_modal_costs"])
        call("run_port_gravity_model", phm.run_port_gravity_model, **plan["run_port_gravity_model"])
    freight_rows = check_outputs_identity(record, plan["compute_modal_costs"]["output_csv"], "freight")
    gravity_rows = check_outputs_identity(record, plan["run_port_gravity_model"]["output_csv"], "gravity")
    return {"freight_rows": freight_rows, "gravity_rows": gravity_rows, "plan": plan,
            "gravity_witness_events": fcp.events}


def run_slot_body(CONFIG, mark):
    slot = CONFIG["slot"]
    out = CONFIG["out_dir"]
    data_dir = Path(CONFIG["data_dir"]) if CONFIG.get("data_dir") else None
    # defence in depth: the repository is an import root only, never a data or cache location
    repo_real = os.path.realpath(CONFIG["repo_root"]) + "/"
    for key in ("data_dir", "cache_dir", "out_dir", "district_source", "population_source", "transfer_record"):
        if CONFIG.get(key) and (os.path.realpath(CONFIG[key]) + "/").startswith(repo_real):
            raise W0Stop("config_path_under_repository", key)
    mark("phase", name="imports")
    import scripts.analyze.generate_isochrones as iso
    import scripts.analyze.intermodal_cost_engine as ice
    import scripts.analyze.nh_travel_matrix as matrix
    import scripts.analyze.port_hinterland_model as phm
    import scripts.analyze.producer_io as pio
    import scripts.clean.graph_builder as builder
    origin = module_origin_report([iso, ice, matrix, phm, pio, builder], CONFIG["repo_root"])
    if not all(v["inside_repo"] for v in origin.values()):
        raise W0Stop("module_outside_repository", json.dumps(origin)[:300])
    mark("phase", name="imports_done")
    result = {"slot": slot, "module_origin": origin, "versions": {
        "numpy": np.__version__, "pandas": pd.__version__}}
    if slot == "H0":
        mark("phase", name="source_selection")
        loaded = builder.load_highway_source(data_dir)
        if loaded is None:
            raise CacheGuardStop("h0_source_missing", str(data_dir))
        nh, _src = loaded
        mark("phase", name="graph_build")
        result["h0"] = run_h0(builder, nh, CONFIG["cache_dir"], out)
        mark("phase", name="graph_done")
    elif slot.startswith("H1"):
        sample = int(CONFIG["sample_rows"])
        cache_dir, expect = CONFIG["cache_dir"], CONFIG["expected_graph"]
        district_path = data_dir / "administrative/india_districts_lgd.geojson"
        pop_rows = _count_rows(data_dir / "demographic/district_population_estimates.csv")
        guard = H1H2Guard(builder, out, cache_dir, expect, [(matrix, "load_or_build_cached_graph")])
        witness = W0Witness(matrix, pio, district_path, sample, population_rows_expected=pop_rows)
        with guard, witness:
            mark("phase", name="build_travel_outputs")
            summary, port_matrix, _prov = matrix.build_travel_outputs(data_dir=data_dir, cache_dir=cache_dir, chunk_size=32)
        mark("phase", name="write_outputs")
        adir = Path(out) / "analysis"
        paths = {"csv": adir / "nh_district_travel_time_summary.csv", "pq": adir / "nh_district_travel_time_summary.parquet",
                 "mx": adir / "nh_district_port_matrix.csv"}
        matrix.write_travel_outputs(summary, port_matrix, summary_csv=paths["csv"], summary_parquet=paths["pq"],
                                    port_matrix_csv=paths["mx"])
        mark("phase", name="readback")
        rb = verify_readback(witness.ctx, witness.positions, csv_path=paths["csv"], parquet_path=paths["pq"])
        rb["matrix"] = verify_readback(witness.ctx, witness.positions, csv_path=paths["mx"])["csv"]
        check_unchanged(cache_dir, expect, "after_slot")
        verdict = guard.verdict()
        if not verdict["clean"]:
            raise CacheGuardStop("latch_after_slot", json.dumps(verdict)[:300])
        os.makedirs(Path(out) / "context", exist_ok=True)
        _write_json(Path(out) / "context" / "full_identity_context.json", witness.ctx.to_json())
        result.update({"h1": {"rows": len(summary), "readback": rb, "guard": verdict, "witness_events": witness.events,
                              "context_sha256": witness.ctx.sha256, "district_rows_full": witness.ctx.n,
                              "population_rows": pop_rows}})
    elif slot == "H2":
        cache_dir, expect = CONFIG["cache_dir"], CONFIG["expected_graph"]
        guard = H1H2Guard(builder, out, cache_dir, expect, [])
        with guard:
            mark("phase", name="build_isochrones")
            ports, mmlps, _prov = iso.build_isochrones(data_dir=data_dir, cache_dir=cache_dir, chunk_size=32)
        check_unchanged(cache_dir, expect, "after_slot")
        verdict = guard.verdict()
        if not verdict["clean"]:
            raise CacheGuardStop("latch_after_slot", json.dumps(verdict)[:300])
        result["h2"] = {"ports_features": len(ports), "mmlp_features": len(mmlps), "guard": verdict,
                        "ok_geometries": int((ports.status == "ok").sum() + (mmlps.status == "ok").sum())}
    elif slot == "H3":
        if sha256_path(CONFIG["transfer_record"]) != CONFIG.get("transfer_record_sha256"):
            raise TransferStop("transfer_record_not_the_registered_one", "")
        record = load_json(CONFIG["transfer_record"])
        mark("phase", name="transfer_validation")
        res = h3_enter_models(record, Path(out) / "h3", CONFIG["district_source"], CONFIG["population_source"],
                              ice, phm, pio)
        result["h3"] = res
    else:
        raise CacheGuardStop("unknown_slot", slot)
    return result


def run_slot(CONFIG, mark, NONCE):
    out = CONFIG["out_dir"]
    stops = (W0Stop, CacheGuardStop, TransferStop, ForbiddenBuild)
    try:
        result = run_slot_body(CONFIG, mark)
    except stops as e:
        code = getattr(e, "code", type(e).__name__)
        _write_json(os.path.join(out, "slot_stop.json"), {"code": code, "detail": str(getattr(e, "detail", e))[:300]})
        mark("error", kind="N2_" + str(code))
        mark("exiting", code=1)
        os._exit(1)
    mark("phase", name="serialize")
    _write_json(os.path.join(out, "slot_result.json"), result)
    _write_json(os.path.join(out, "completion_receipt.json"), {"ok": True, "nonce": NONCE, "slot": result["slot"]})
    mark("phase", name="done")
