# N2 step 4a control-to-test matrix

Addendum r4 SHA-256 26d285927cf26d8317fd32206ae57b757045950d81259e885f395f724831969f (verified before start).
Status values: planned | implemented | not-applicable-4a. Step 4b: M05, M06, M07 and the carry-forward / (c) / (d) rows are implemented (tests/ = stdlib suite, tests4b/ = venv suite).
A consistency test (`test_MATRIX_consistency`) asserts every test name below exists and every
`test_*` method is listed here. All numeric policy values used by tests are SYNTHETIC_TEST_POLICY_NOT_APPROVED.

| Control | Test name | Status |
|---|---|---|
| M01 | test_M01_mode_selftest_roots_confined | implemented |
| M01 | test_M01_mode_prepare_metadata_only_no_content_open | implemented |
| M01 | test_M01_prepare_refuses_national_paths | implemented |
| M01 | test_M01_run_refuses_without_receipts | implemented |
| M01 | test_M01_run_refuses_missing_approval | implemented |
| M01 | test_M01_run_refuses_changed_approval_hash | implemented |
| M01 | test_M01_run_refuses_changed_policy | implemented |
| M01 | test_M01_run_refuses_changed_scope | implemented |
| M01 | test_M01_run_refuses_changed_harness_hash | implemented |
| M01 | test_M01_interpreter_flags_enforced | implemented |
| M01 | test_M01_run_refuses_missing_ledger | implemented |
| M01 | test_M01_run_refuses_unbound_receipts | implemented |
| M01 | test_M01_run_refuses_nonfinite_scope | implemented |
| M01 | test_M01_run_refusal_creates_no_child_or_cgroup | implemented |
| M01 | test_M01_run_nonmock_valid_receipts_refused_no_approved_receipt | implemented |
| M01 | test_M01_run_mock_receipts_accepted_by_gate | implemented |
| M01 | test_M01_run_mock_receipts_dispatch_end_to_end | implemented |
| M02 | test_M02_attempt_exclusive_create | implemented |
| M02 | test_M02_attempt_record_immutable_mode_and_no_pid | implemented |
| M02 | test_M02_attempt_key_independent_of_run_label | implemented |
| M02 | test_M02_launch_failure_consumes_attempt | implemented |
| M02 | test_M02_renamed_run_label_does_not_reset | implemented |
| M02 | test_M02_moved_root_refused | implemented |
| M02 | test_M02_fresh_root_refused | implemented |
| M02 | test_M02_persist_failure_blocks_dispatch | implemented |
| M02 | test_M02_six_slot_order_and_dependencies | implemented |
| M02 | test_M02_no_later_slot_before_terminal_receipt | implemented |
| M02 | test_M02_exit_log_terminal_sequence | implemented |
| M02 | test_M02_failed_earlier_slot_blocks_without_consuming | implemented |
| M03-L1 | test_M03_L1_attempt_record_precedes_mkdir | implemented |
| M03-L1 | test_M03_L1_eexist_stops | implemented |
| M03-L2 | test_M03_L2_limits_page_aligned_order_readback | implemented |
| M03-L2 | test_M03_L2_injected_write_failure_consumes_and_removes_own_cgroups | implemented |
| M03-L3 | test_M03_L3_fork_refused_multi_thread | implemented |
| M03-L3 | test_M03_L3_fork_refused_module_outside_allowlist | implemented |
| M03-L3 | test_M03_L3_fork_refused_sigchld_ign | implemented |
| M03-L3 | test_M03_L3_gc_disabled_and_stdio_flushed | implemented |
| M03-L4 | test_M03_L4_gated_child_not_exec | implemented |
| M03-L4 | test_M03_L4_fd_set_exact | implemented |
| M03-L4 | test_M03_L4_listdir_transient_duplicate_controls | implemented |
| M03-L4 | test_M03_L4_gate_w_ebadf | implemented |
| M03-L4 | test_M03_L4_exec_fd_inplace_rewrite_detected | implemented |
| M03-L4 | test_M03_L4_stub_source_change_refused | implemented |
| M03-L4 | test_M03_L4_extra_fd_E_FD | implemented |
| M03-L4 | test_M03_L4_exec_fd_swap_E_EXEC | implemented |
| M03-L4 | test_M03_L4_gate_eof_E_GATE_and_wrong_byte | implemented |
| M03-L4 | test_M03_L4_child_exception_os_exit_E_INTERNAL | implemented |
| M03-L4 | test_M03_L4_signal_handlers_default | implemented |
| M03-L5 | test_M03_L5_joins_ordered_and_verified | implemented |
| M03-L5 | test_M03_L5_join_failure_R1_paths | implemented |
| M03-L5 | test_M03_L5_pidfd_open_failure_stop | implemented |
| M03-L6 | test_M03_L6_launch_record_and_k_samples_before_release | implemented |
| M03-L6 | test_M03_L6_telemetry_failure_at_S1_cleanup | implemented |
| M03-L7 | test_M03_L7_stub_refusal_wrong_cgroup | implemented |
| M03-L7 | test_M03_L7_stub_refusal_nspid_field_count | implemented |
| M03-L7 | test_M03_L7_stub_refusal_nspid_last_not_1 | implemented |
| M03-L7 | test_M03_L7_stub_refusal_no_pdeathsig | implemented |
| M03-L7 | test_M03_L7_stub_refusal_env_differs | implemented |
| M03-L7 | test_M03_L7_stub_refusal_exe_identity_or_hash | implemented |
| M03-L8 | test_M03_L8_stub_only_fds_0_2 | implemented |
| M03-L9 | test_M03_L9_graceful_path | implemented |
| M03-L9 | test_M03_L9_hard_path_option_a | implemented |
| M03-L9 | test_M03_L9_aplus_cgroup_kill_backstop | implemented |
| M03-L9 | test_M03_L9_v2_kill_probe_available | implemented |
| M03-L10 | test_M03_L10_site_equivalence | implemented |
| M03-L11 | test_M03_L11_unrelated_sleeper_survives | implemented |
| M03-L11 | test_M03_L11_static_no_raw_kill | implemented |
| M03-L12 | test_M03_L12_exec_fd_hash_mismatch_refused | implemented |
| M03-L12 | test_M03_L12_unshare_exec_failure | implemented |
| M03-L12 | test_M03_L12_newpid_eperm_injected | implemented |
| M03-L13 | (manual smoke: Bash-tool timeout kill of a paused controller; scripts/l13_observe.py; evidence in report) | manual-smoke |
| M03-n1 | test_M03_note1_cmdline_index_and_argv_wording | implemented |
| M03-n2 | test_M03_note2_stub_ascii_size_no_secrets | implemented |
| M03-n3 | test_M03_note3_fcntl_allowlisted_one_discarded | implemented |
| M03-n4 | test_M03_note4_subreaper_set_before_fork | implemented |
| M03-n4 | test_M03_note4_static_no_wait_any | implemented |
| M03-n5 | test_M03_note5_pidns_dev_ino_and_reconfirm | implemented |
| M03 Windows items | Job Object / CREATE_SUSPENDED / IsProcessInJob | not-applicable-4a (replaced by M03-L) |
| M04 | test_M04_disjoint_roots | implemented |
| M04 | test_M04_symlink_ancestor_and_leaf_rejected | implemented |
| M04 | test_M04_mountpoint_ancestor_rejected | implemented |
| M04 | test_M04_sys_proc_dev_rejected | implemented |
| M04 | test_M04_missing_nonregular_swapped_paths | implemented |
| M04 | test_M04_no_fallback_roots | implemented |
| M08-L | test_M08_unit_conversions_kb_bytes_max_token_sentinel | implemented |
| M08-L | test_M08_four_memory_fields_distinct_no_commit_name | implemented |
| M08-L | test_M08_read_failure_alive_child_stops | implemented |
| M08-L | test_M08_missing_live_member_containment_stop | implemented |
| M08-L | test_M08_descendant_outside_listing_stop | implemented |
| M08-L | test_M08_sample_sequence_missed_dup_out_of_order_clock_back | implemented |
| M08-L | test_M08_effective_available_mock_sysfs | implemented |
| M08-L | test_M08_bash_growth_mock_sysfs | implemented |
| M08-L | test_M08_memsw_check_only_after_exit | implemented |
| M08-L | test_M08_residency_from_mock_mincore_vectors | implemented |
| M08-L | test_M08_real_sample_fields_complete | implemented |
| M09-L | test_M09_floor_crossings_strict_inclusive | implemented |
| M09-L | test_M09_sample_floors_every_field_strict_inclusive | implemented |
| M09-L | test_M09_gap_and_elapsed_expiry | implemented |
| M09-L | test_M09_mock_counters_oom_pidsmax_eagain_enospc_libload | implemented |
| M09-L | test_M09_cache_latch | implemented |
| M09-L | test_M09_exit_without_receipt | implemented |
| M09-L | test_M09_failcnt_and_memsw_actions | implemented |
| M09-L | test_M09_markers_without_nonce_ignored_flagged | implemented |
| M09-L | test_M09_neutral_injection_labels_only | implemented |
| M09-L | test_M09_reserve_gates_launch | implemented |
| M09-L | test_M09_real_elapsed_cap_terminates | implemented |
| M09-L | test_M09_real_floor_breach_terminates | implemented |
| M10 | test_M10_phase_witness_only_declared | implemented |
| M10 | test_M10_scalar_retention | implemented |
| M10 | test_M10_peak_includes_setup_and_serialization | implemented |
| M10 | test_M10_partial_output_grants_no_authority | implemented |
| M11 | test_M11_repo_preserved | implemented |
| M11 | test_M11_scratch_manifest_not_publication_manifest | implemented |
| M11 | test_M11_logs_exact_hashes | implemented |
| M11 | test_M11_no_cgroup_state_in_repo | implemented |
| M12 | test_M12_existing_n2_cgroup_stops_no_cleanup | implemented |
| M12 | test_M12_names_unique | implemented |
| M12 | test_M12_failed_rmdir_stops | implemented |
| M12 | test_M12_controller_cgroup_path_differs_stops | implemented |
| M12 | test_M12_dying_memcg_reported | implemented |
| M12 | option (c) frozen-orphan recovery | not-applicable-4a (D-3 option a+, no freezer) |
| M13 | test_M13_kill_controller_at_S0 | implemented |
| M13 | test_M13_kill_controller_at_S1 | implemented |
| M13 | test_M13_kill_controller_after_release | implemented |
| M13 | test_M13_main_thread_exit_pdeathsig | implemented |
| M13 | test_M13_sigterm_to_process_group_graceful_terminal_receipt | implemented |
| M13 | test_M13_grandchild_setsid_ignoring_sigterm_killed | implemented |
| M13 | test_M13_reaping_order_parent_death_path | implemented |
| M13 | test_M13_reaping_order_hard_path | implemented |
| M13 | test_M13_mock_unowned_member_reported_not_killed | implemented |
| M13 | test_M13_cgroup_kill_skipped_when_unowned_present | implemented |
| M13 | test_M13_unshare_killed_only_after_bounded_wait | implemented |
| M13 | test_M13_mock_pidns_mismatch_not_signalled | implemented |
| M13 | v1-frozen / v2-frozen variants | not-applicable-4a (no freeze is ever applied in option a+) |
| M14 | test_M14_charges_before_join_excluded | implemented |
| M14 | test_M14_alloc_after_join_in_peak_and_anon | implemented |
| M14 | test_M14_file_io_cache_not_anon | implemented |
| M14 | test_M14_fadvise_mincore_drop_measurable | implemented |
| M14 | test_M14_mincore_positive_control | implemented |
| M14 | test_M14_mincore_ownership_mode_control | implemented |
| M14 | test_M14_resident_elsewhere_nonzero_after_import | implemented |
| M14 | test_M14_kmem_included_in_usage_probe | implemented |
| M14 | test_M14_residual_after_exit | implemented |
| M15 | test_M15_oom_variant | implemented |
| M15 | test_M15_non_oom_variant | implemented |
| M16 | test_M16_self_migration_mock_stop | implemented |
| M16 | test_M16_thread_moved_mock_stop | implemented |
| M16 | test_M16_unexpected_process_in_cgroup_stops | implemented |
| M16 | test_M16_real_thread_moved_via_tasks_detected | implemented |
| M16 | test_M16_D4_readonly_cgroup_write_fails | implemented |
| M16 | test_M16_D4_caps_dropped | implemented |
| M16 | test_M16_env_scrub | implemented |
| M16 | test_M16_thread_vars_D1 | implemented |
| M09-L | test_M09_post_exit_failcnt_last_interval | implemented (review round 1) |
| M09-L | test_M09_post_exit_conservative_peak_last_interval | implemented (review round 1) |
| M09-L | test_M09_post_exit_gates_classify_unit | implemented (review round 1) |
| M09-L | test_M09_receipt_mandatory_for_non_mock | implemented (review round 1) |
| M09-L | test_M09_swap_gate | implemented (review round 1) |
| M12 | test_M12_foreign_cgroup_survives_remove | implemented (review round 1) |
| M13 | test_M13_nonzero_exit_requires_nonce_framed_marker | implemented (review round 1) |
| M16 | test_M16_D4_erofs_specific_and_fstype_selection | implemented (review round 1) |
| M16 | test_M16_pf_exiting_absorbed_and_recorded | implemented (review round 1) |
| M16 | test_M16_nsinit_discovery_requires_exact_nspid_length | implemented (review round 1) |
| M09-L | test_M09_final_sample_failure_is_recorded_and_stops | implemented (review round 2) |
| M09-L | test_M09_synthetic_mock_default_is_false | implemented (review round 2) |
| M12 | test_M12_stat_failure_rolls_back_created_dirs | implemented (review round 2) |
| M16 | test_M16_exiting_leader_does_not_absorb_live_thread | implemented (review round 2) |
| M09-L | test_M09_post_exit_disk_floor_persists | implemented (review round 3) |
| M09-L | test_M09_breach_then_nonzero_exit_is_stopped | implemented (review round 3) |
| M09-L | test_M09_failed_final_reads_fail_closed | implemented (review round 4) |
| M09-L | test_M09_malformed_counter_does_not_escape_gates | implemented (review round 4) |
| M09-L | test_M09_telemetry_close_failure_fails_closed | implemented (review round 5) |
| M09-L | test_M09_unexpected_exception_types_still_write_terminal | implemented (review round 5) |
| M02 | test_M02_serialize_failure_leaves_no_file_and_no_fd | implemented (review round 5) |
| M13 | test_M13_terminate_continues_escalating_on_read_errors | implemented (review round 5) |
| M12 | test_M12_rollback_failure_is_recorded | implemented (review round 5) |
| M09-L | test_M09_late_marker_error_events_are_kept | implemented (review round 5) |
| M09-L | test_M09_post_exit_unexpected_exception_on_stop_path | implemented (review round 6) |
| M02 | test_M02_close_failure_does_not_escape_as_ebadf | implemented (review round 6) |
| M09-L | test_M09_fallback_stop_lands_in_self_stops | implemented (review round 6) |
| M09-L | test_M09_finalize_hooks_compose | implemented (review round 6) |
| META | test_MATRIX_consistency | implemented |
| 4B-CF | test_4B_after_attempt_record_hook_inside_guarded_try | implemented (4b, tests) |
| 4B-CF | test_4B_hostile_str_does_not_replace_outcome | implemented (4b, tests) |
| 4B-CF | test_4B_member_close_failure_is_guarded | implemented (4b, tests) |
| 4B-CF | test_4B_ledger_close_oserror_does_not_replace_ledgerstop | implemented (4b, tests) |
| 4B-CF | test_4B_short_write_is_completed_and_zero_write_fails | implemented (4b, tests) |
| 4B-CF | test_4B_minimal_body_serialize_failure_blocks_dispatch | implemented (4b, tests) |
| 4B-CF | test_4B_hooks_compose_not_overwrite | implemented (4b, tests) |
| 4B-CF | test_4B_elapsed_cap_evaluated_post_exit | implemented (4b, tests) |
| 4B | test_4B_n2h4b_parts_ascii_no_raw_kill_no_repo_data_paths | implemented (4b, tests) |
| 4B | test_4B_harness_hash_binds_n2h4b_and_transfer | implemented (4b, tests) |
| 4B-RUN | test_4B_real_dispatch_refused_at_gate_no_approved_receipt | implemented (4b, tests) |
| 4B-RUN | test_4B_real_claimed_approval_still_refused_pending_coordinator | implemented (4b, tests) |
| 4B-RUN | test_4B_runner_refuses_non_mock_gate_by_itself | implemented (4b, tests) |
| 4B-RUN | test_4B_runner_config_has_no_driver_only_knobs | implemented (4b, tests) |
| 4B-RUN | test_4B_cli_passes_prepare_recorded_controller_paths | implemented (4b, tests) |
| 4B-C | test_4B_payload_uid_not_root_and_cannot_write_root_owned | implemented (4b, tests) |
| 4B-C | test_4B_uid_untraversable_scratch_refused_before_attempt | implemented (4b, tests) |
| 4B-C | test_4B_uid_zero_or_unpaired_refused | implemented (4b, tests) |
| 4B-C | test_4B_latch_in_uid_out_dir_with_exit_zero_fails | implemented (4b, tests) |
| 4B-D | test_4B_private_copy_leaves_shared_sources_untouched_and_unevicted | implemented (4b, tests) |
| 4B-D | test_4B_private_inputs_refusals | implemented (4b, tests) |
| 4B | test_4B_controller_records_shared_residency_in_conservative_figure | implemented (4b, tests) |
| 4B | test_4B_slot_table_and_payload_builder | implemented (4b, tests) |
| 4B | test_4B_installed_versions_equal_the_recorded_freeze | implemented (4b, tests4b) |
| M06 | test_M06_contained_h0_cold_build_and_parent_hash | implemented (4b, tests4b) |
| M05 | test_M05_contained_h1_witness_inside_containment | implemented (4b, tests4b) |
| M06 | test_M06_contained_h1_h2_guard_clean_and_archive_unchanged | implemented (4b, tests4b) |
| M07 | test_M07_contained_h3_two_domain_transfer_then_models | implemented (4b, tests4b) |
| 4B-C | test_4B_contained_runs_as_unprivileged_uid_with_site_main | implemented (4b, tests4b) |
| M10 | test_M10_contained_only_declared_phase_boundaries | implemented (4b, tests4b) |
| 4B | test_4B_module_origin_and_versions_recorded_in_child | implemented (4b, tests4b) |
| M05 | test_M05_contained_lossy_code_roundtrip_stops_slot | implemented (4b, tests4b) |
| M06 | test_M06_contained_tampered_cache_stops_before_any_work | implemented (4b, tests4b) |
| M06 | test_M06_contained_missing_cache_never_rebuilds | implemented (4b, tests4b) |
| M03-L10 | test_M03_L10_site_equivalence_venv | implemented (4b, tests4b) |
| M05 | test_M05_imports_resolve_inside_repository | implemented (4b, tests4b) |
| M05 | test_M05_parity_cross_prefix_collision | implemented (4b, tests4b) |
| M05 | test_M05_same_state_duplicate_outside_prefix_stops | implemented (4b, tests4b) |
| M05 | test_M05_uncoded_and_null_population_stay_literal | implemented (4b, tests4b) |
| M05 | test_M05_literal_labels_through_csv_and_parquet | implemented (4b, tests4b) |
| M05 | test_M05_lossy_large_code_roundtrip_stops | implemented (4b, tests4b) |
| M05 | test_M05_non_default_index_restored_and_checked | implemented (4b, tests4b) |
| M05 | test_M05_both_bindings_wrapped_delegated_and_restored | implemented (4b, tests4b) |
| M05 | test_M05_incoming_mismatch_and_trimmed_population_stop | implemented (4b, tests4b) |
| M05 | test_M05_no_retained_geometry | implemented (4b, tests4b) |
| M06 | test_M06_h0_cold_build_closes_archive_and_captures_fingerprint | implemented (4b, tests4b) |
| M06 | test_M06_h0_refuses_present_cache_and_latched_warning | implemented (4b, tests4b) |
| M06 | test_M06_warm_loads_through_both_dispatch_paths | implemented (4b, tests4b) |
| M06 | test_M06_h1_and_h2_producers_run_through_the_guard | implemented (4b, tests4b) |
| M06 | test_M06_missing_cache_hits_sentinel_and_latches | implemented (4b, tests4b) |
| M06 | test_M06_tampered_cache_refused_before_and_after | implemented (4b, tests4b) |
| M06 | test_M06_other_source_fingerprint_forces_rebuild_which_is_forbidden | implemented (4b, tests4b) |
| M06 | test_M06_corrupt_archive_accepted_by_hash_still_warns_and_latches | implemented (4b, tests4b) |
| M06 | test_M06_warning_latch_is_nonraising_and_severity_filtered | implemented (4b, tests4b) |
| M06 | test_M06_latch_write_failure_is_remembered_not_raised | implemented (4b, tests4b) |
| M06 | test_M06_swallowed_sentinel_exception_still_fails_the_slot | implemented (4b, tests4b) |
| M06 | test_M06_no_repository_or_default_cache_fallback | implemented (4b, tests4b) |
| M06 | test_M06_non_real_loader_binding_refused | implemented (4b, tests4b) |
| M07 | test_M07_two_domains_membership_and_exact_h3_arguments | implemented (4b, tests4b) |
| M07 | test_M07_gravity_population_uses_full_context_and_keeps_nulls | implemented (4b, tests4b) |
| M07 | test_M07_sample_outputs_identities_match_exactly | implemented (4b, tests4b) |
| M07 | test_M07_tampered_summary_refused_before_model_entry | implemented (4b, tests4b) |
| M07 | test_M07_missing_matrix_refused | implemented (4b, tests4b) |
| M07 | test_M07_null_population_drift_refused | implemented (4b, tests4b) |
| M07 | test_M07_order_drift_in_h1_summary_refused_at_transfer | implemented (4b, tests4b) |
| M07 | test_M07_trimmed_full_context_or_population_refused | implemented (4b, tests4b) |
| M07 | test_M07_context_identity_not_original_refused | implemented (4b, tests4b) |
| M07 | test_M07_population_not_original_refused | implemented (4b, tests4b) |
| M07 | test_M07_transfer_root_rules | implemented (4b, tests4b) |
| M07 | test_M07_rec_revalidated_at_model_entry | implemented (4b, tests4b) |
| M11 | test_M11_repository_data_is_masked_for_every_reader | implemented (4b, tests4b) |
| 4B-R1-B1 | test_4B_config_repo_data_dir_refused_before_any_attempt | implemented (4b review 1, tests) |
| 4B-R1-B1 | test_4B_config_repo_cache_dir_refused_before_any_attempt | implemented (4b review 1, tests) |
| 4B-R1-B1 | test_4B_config_missing_payload_uid_refused | implemented (4b review 1, tests) |
| 4B-R1-B1 | test_4B_config_other_refusals | implemented (4b review 1, tests) |
| 4B-R1-B2 | test_4B_symlinked_archive_refused_and_target_untouched | implemented (4b review 1, tests) |
| 4B-R1-B2 | test_4B_cache_dir_content_rules | implemented (4b review 1, tests) |
| 4B-R1-B2 | test_4B_other_root_side_reads_refuse_symlinks | implemented (4b review 1, tests) |
| 4B-R1-B4 | test_4B_parent_receipts_gate_later_slots | implemented (4b review 1, tests) |
| 4B-R1-B4 | test_4B_h3_needs_registered_transfer_record | implemented (4b review 1, tests) |
| 4B-R1-N | test_4B_membership_transient_disagreement_counted | implemented (4b review 1, tests) |
| 4B-R1-N | test_4B_terminal_body_carries_transient_disagreement_count | implemented (4b review 1, tests) |
| 4B-R1-N | test_4B_fadvise_refuses_inodes_the_attempt_did_not_create | implemented (4b review 1, tests) |
| 4B-R1-B4 | test_B4_failed_parent_verification_blocks_h1_even_though_h0_child_succeeded | implemented (4b review 1, tests4b) |
| 4B-R1-B4 | test_B4_h1_requires_config_graph_hash_to_equal_the_h0_receipt | implemented (4b review 1, tests4b) |
| 4B-R1-B4 | test_B4_h3_needs_registered_transfer_record_and_config_sha | implemented (4b review 1, tests4b) |
| 4B-R1-B1 | test_B1_slot_code_refuses_repository_paths_itself | implemented (4b review 1, tests4b) |
| 4B-R1-N | test_sweep_detects_stale_scratch_dirs_and_runner_has_results_dir | implemented (4b review 1, tests4b) |
| 4B-R1-N | test_N2_run_h0_checks_logger_enabled | implemented (4b review 1, tests4b) |
| 4B-R1-N | test_N3_h3_witness_requires_and_restores_the_real_helper | implemented (4b review 1, tests4b) |
| 4B-R1-N | test_N4_positions_and_registered_record_checked | implemented (4b review 1, tests4b) |
| M11 | test_M11_repository_data_is_masked_for_every_reader | implemented (4b review 1, tests4b) |
| M11 | test_M11_no_repository_data_opened_by_python_in_the_suite | implemented (4b review 1, tests4b) |
| M11 | test_M11_repository_working_tree_unchanged_outside_masked_dirs | implemented (4b review 1, tests4b) |
