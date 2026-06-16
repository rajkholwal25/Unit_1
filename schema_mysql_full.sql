-- Unit 1 full schema (MySQL 8+)
-- Use this file on your MySQL server
-- Tables: 28 + indexes
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;


CREATE TABLE bom_templates (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	template_name VARCHAR(128) NOT NULL, 
	process_sequence JSON NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE coating_types (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(16) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	is_active BOOL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
)

;


CREATE TABLE material_types (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	code VARCHAR(10) NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	is_active BOOL, 
	PRIMARY KEY (id), 
	UNIQUE (code)
)

;


CREATE TABLE patterns (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	pattern_code VARCHAR(10) NOT NULL, 
	pattern_name VARCHAR(128) NOT NULL, 
	is_active BOOL, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (pattern_code)
)

;


CREATE TABLE process_master (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	process_code VARCHAR(20) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	category VARCHAR(50), 
	default_workcenter VARCHAR(20), 
	is_active BOOL NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE sap_customer_mirror (
	card_code VARCHAR(30) NOT NULL, 
	card_name VARCHAR(200), 
	phone VARCHAR(100), 
	email VARCHAR(120), 
	synced_at DATETIME NOT NULL, 
	PRIMARY KEY (card_code)
)

;


CREATE TABLE sap_item_mirror (
	item_code VARCHAR(50) NOT NULL, 
	item_name VARCHAR(200), 
	item_type ENUM('fg','raw_material','consumable','service'), 
	uom VARCHAR(10), 
	default_warehouse VARCHAR(20), 
	synced_at DATETIME NOT NULL, 
	PRIMARY KEY (item_code)
)

;


CREATE TABLE sap_mirror_sync_state (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	last_full_sync_at DATETIME, 
	last_customer_sync_at DATETIME, 
	last_item_sync_at DATETIME, 
	customer_row_count INTEGER, 
	item_row_count INTEGER, 
	last_error TEXT, 
	PRIMARY KEY (id)
)

;


CREATE TABLE sap_push_logs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	request_payload JSON, 
	response_payload JSON, 
	status VARCHAR(32), 
	created_at DATETIME, 
	PRIMARY KEY (id)
)

;


CREATE TABLE users (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	username VARCHAR(80), 
	email VARCHAR(256) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	`role` VARCHAR(32) NOT NULL, 
	is_active_user BOOL, 
	created_at DATETIME, 
	last_login_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (email)
)

;


CREATE TABLE generated_fg_items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	item_code VARCHAR(128) NOT NULL, 
	material_type VARCHAR(32) NOT NULL, 
	thickness NUMERIC(10, 3) NOT NULL, 
	coating VARCHAR(16), 
	pattern_id INTEGER, 
	bom_template_id INTEGER, 
	raw_material_item_code VARCHAR(128), 
	yield_loss_pct NUMERIC(5, 2) NOT NULL, 
	sap_bom_pushed_at DATETIME, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_fg_item_code_template UNIQUE (item_code, bom_template_id), 
	FOREIGN KEY(pattern_id) REFERENCES patterns (id), 
	FOREIGN KEY(bom_template_id) REFERENCES bom_templates (id)
)

;


CREATE TABLE job_cards (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_card_number VARCHAR(20) NOT NULL, 
	sap_customer_code VARCHAR(30) NOT NULL, 
	product_name VARCHAR(200) NOT NULL, 
	product_description TEXT, 
	item_code VARCHAR(50), 
	quantity FLOAT NOT NULL, 
	uom VARCHAR(20), 
	delivery_date DATE, 
	priority ENUM('low','medium','high','urgent') NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	sap_production_order VARCHAR(50), 
	sap_bom_number VARCHAR(50), 
	sap_so_doc_num VARCHAR(50), 
	sap_so_doc_entry INTEGER, 
	sap_mjd1_line_code VARCHAR(120), 
	sap_fg_code VARCHAR(100), 
	sap_fg_name_snap VARCHAR(200), 
	sap_selected_lines_json TEXT, 
	process_sequence_json TEXT, 
	carton_length_mm FLOAT, 
	carton_width_mm FLOAT, 
	carton_height_mm FLOAT, 
	job_kind VARCHAR(20), 
	sap_po_doc_entries_json TEXT, 
	created_by INTEGER NOT NULL, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(sap_customer_code) REFERENCES sap_customer_mirror (card_code), 
	FOREIGN KEY(created_by) REFERENCES users (id)
)

;


CREATE TABLE job_master (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_no VARCHAR(20) NOT NULL, 
	sap_customer_code VARCHAR(20), 
	sap_customer_name_snap VARCHAR(200), 
	sap_so_entry INTEGER, 
	sap_so_number_snap VARCHAR(20), 
	sap_job_card_doc_entry INTEGER, 
	sap_job_card_doc_num_snap VARCHAR(30), 
	sap_job_card_series_snap VARCHAR(30), 
	sap_job_card_title_snap VARCHAR(200), 
	overall_status VARCHAR(30) NOT NULL, 
	priority ENUM('low','normal','urgent') NOT NULL, 
	job_type_cat VARCHAR(20), 
	job_series VARCHAR(20), 
	original_job_no VARCHAR(20), 
	delivery_date DATE, 
	remarks TEXT, 
	assigned_planner_id INTEGER, 
	created_by INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(assigned_planner_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT
)

;


CREATE TABLE roll_grn_entry (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	grn_number VARCHAR(16) NOT NULL, 
	supplier_name VARCHAR(200) NOT NULL, 
	supplier_roll_number VARCHAR(100) NOT NULL, 
	film_type VARCHAR(50) NOT NULL, 
	coating VARCHAR(50) NOT NULL, 
	width_mm NUMERIC(12, 3) NOT NULL, 
	thickness_mic NUMERIC(12, 3) NOT NULL, 
	length_mtr NUMERIC(12, 3) NOT NULL, 
	gross_weight_kg NUMERIC(12, 3) NOT NULL, 
	net_weight_kg NUMERIC(12, 3) NOT NULL, 
	core_weight_kg NUMERIC(12, 3), 
	created_at DATETIME NOT NULL, 
	created_by_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by_id) REFERENCES users (id)
)

;


CREATE TABLE bom_structures (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	generated_fg_id INTEGER, 
	parent_item_code VARCHAR(128) NOT NULL, 
	child_item_code VARCHAR(128) NOT NULL, 
	process_sequence JSON, 
	line_type VARCHAR(16) NOT NULL, 
	quantity NUMERIC(12, 6), 
	sort_order INTEGER NOT NULL, 
	warehouse_code VARCHAR(64), 
	PRIMARY KEY (id), 
	FOREIGN KEY(generated_fg_id) REFERENCES generated_fg_items (id)
)

;


CREATE TABLE generated_process_items (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	fg_item_id INTEGER, 
	process_code VARCHAR(32) NOT NULL, 
	item_code VARCHAR(128) NOT NULL, 
	warehouse_code VARCHAR(64), 
	PRIMARY KEY (id), 
	FOREIGN KEY(fg_item_id) REFERENCES generated_fg_items (id)
)

;


CREATE TABLE integration_event (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_id VARCHAR(20) NOT NULL, 
	target_system VARCHAR(20) NOT NULL, 
	action VARCHAR(50) NOT NULL, 
	state ENUM('pending','success','failed','retrying') NOT NULL, 
	request_payload JSON, 
	response_payload JSON, 
	error_message TEXT, 
	retry_count INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE CASCADE
)

;


CREATE TABLE item_master (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	item_code VARCHAR(128) NOT NULL, 
	item_name VARCHAR(128) NOT NULL, 
	item_type VARCHAR(16) NOT NULL, 
	parent_fg_code VARCHAR(128), 
	process_code VARCHAR(32), 
	material_type VARCHAR(32), 
	thickness NUMERIC(10, 3), 
	coating VARCHAR(16), 
	pattern_id INTEGER, 
	bom_template_id INTEGER, 
	generated_fg_id INTEGER, 
	warehouse_code VARCHAR(64), 
	items_group_code INTEGER, 
	invntry_uom VARCHAR(16), 
	sal_unit_msr VARCHAR(16), 
	buy_unit_msr VARCHAR(16), 
	sales_item BOOL, 
	sap_pushed BOOL, 
	sap_pushed_at DATETIME, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(pattern_id) REFERENCES patterns (id), 
	FOREIGN KEY(bom_template_id) REFERENCES bom_templates (id), 
	FOREIGN KEY(generated_fg_id) REFERENCES generated_fg_items (id)
)

;


CREATE TABLE job_card_materials (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_card_id INTEGER NOT NULL, 
	material_name VARCHAR(200) NOT NULL, 
	material_code VARCHAR(50), 
	paper_type VARCHAR(100), 
	gsm VARCHAR(20), 
	width_mm FLOAT, 
	height_mm FLOAT, 
	length_mm FLOAT, 
	ink_colors VARCHAR(200), 
	quantity_required FLOAT, 
	uom VARCHAR(20), 
	remarks TEXT, 
	num_ups INTEGER, 
	element_name VARCHAR(200), 
	raw_material_item_code VARCHAR(50), 
	paper_brand VARCHAR(200), 
	total_sheets FLOAT, 
	paper_supplied_by VARCHAR(50), 
	wastage_pct FLOAT, 
	wastage_sheets FLOAT, 
	print_style VARCHAR(80), 
	mill VARCHAR(200), 
	detail_special_instructions TEXT, 
	die_no VARCHAR(100), 
	front_colours VARCHAR(200), 
	back_colours VARCHAR(200), 
	pasting_style VARCHAR(200), 
	print_type_metpet VARCHAR(100), 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_card_id) REFERENCES job_cards (id)
)

;


CREATE TABLE job_card_printing_specs (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_card_id INTEGER NOT NULL, 
	plate_size VARCHAR(50), 
	number_of_colors INTEGER, 
	printing_type VARCHAR(50), 
	finishing_type VARCHAR(100), 
	lamination_type VARCHAR(50), 
	cutting_type VARCHAR(100), 
	special_instructions TEXT, 
	PRIMARY KEY (id), 
	UNIQUE (job_card_id), 
	FOREIGN KEY(job_card_id) REFERENCES job_cards (id)
)

;


CREATE TABLE job_card_status_history (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_card_id INTEGER NOT NULL, 
	old_status VARCHAR(20), 
	new_status VARCHAR(20) NOT NULL, 
	changed_by INTEGER NOT NULL, 
	remarks TEXT, 
	changed_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_card_id) REFERENCES job_cards (id), 
	FOREIGN KEY(changed_by) REFERENCES users (id)
)

;


CREATE TABLE job_detail_line (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_id VARCHAR(20) NOT NULL, 
	detail_no INTEGER NOT NULL, 
	ups INTEGER, 
	yield_loss_pct NUMERIC(5, 2), 
	element_name VARCHAR(100), 
	raw_material_item_code VARCHAR(50), 
	paper_brand VARCHAR(100), 
	mill VARCHAR(100), 
	total_sheets INTEGER, 
	paper_supplied_by ENUM('customer','company'), 
	wastage_pct NUMERIC(5, 2), 
	wastage_sheets INTEGER, 
	sheet_length NUMERIC(8, 2), 
	sheet_width NUMERIC(8, 2), 
	gsm INTEGER, 
	thickness_mic NUMERIC(6, 2), 
	chemical_coating_gsm NUMERIC(8, 3), 
	metallisation_gsm NUMERIC(8, 3), 
	chemical_item_code VARCHAR(50), 
	chemical_qty_kg NUMERIC(10, 3), 
	metallisation_qty_kg NUMERIC(10, 3), 
	print_style VARCHAR(50), 
	print_type VARCHAR(50), 
	front_colours VARCHAR(100), 
	back_colours VARCHAR(100), 
	die_no VARCHAR(50), 
	pasting_style VARCHAR(50), 
	special_instructions TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_job_detail_no UNIQUE (job_id, detail_no), 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE CASCADE
)

;


CREATE TABLE job_header_line (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_id VARCHAR(20) NOT NULL, 
	line_no INTEGER NOT NULL, 
	sap_fg_item_code VARCHAR(50), 
	sap_fg_item_name_snap VARCHAR(200), 
	dispatch_qty NUMERIC(12, 3), 
	length NUMERIC(10, 2), 
	width NUMERIC(10, 2), 
	height NUMERIC(10, 2), 
	uom VARCHAR(10), 
	ups INTEGER, 
	job_type VARCHAR(30), 
	released_at DATETIME, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_job_line_no UNIQUE (job_id, line_no), 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE CASCADE
)

;


CREATE TABLE job_status_history (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_id VARCHAR(20) NOT NULL, 
	from_status VARCHAR(30), 
	to_status VARCHAR(30) NOT NULL, 
	changed_at DATETIME NOT NULL, 
	changed_by INTEGER NOT NULL, 
	reason TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE CASCADE, 
	FOREIGN KEY(changed_by) REFERENCES users (id) ON DELETE RESTRICT
)

;


CREATE TABLE bom (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	detail_line_id INTEGER NOT NULL, 
	job_id VARCHAR(20), 
	version INTEGER NOT NULL, 
	is_active BOOL NOT NULL, 
	created_by INTEGER, 
	created_at DATETIME NOT NULL, 
	slip_process_sequence_json TEXT, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_bom_version UNIQUE (detail_line_id, version), 
	FOREIGN KEY(detail_line_id) REFERENCES job_detail_line (id) ON DELETE CASCADE, 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE SET NULL, 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
)

;


CREATE TABLE job_detail_line_fg_involved (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	job_id VARCHAR(20) NOT NULL, 
	detail_line_id INTEGER NOT NULL, 
	header_line_id INTEGER NOT NULL, 
	fg_num VARCHAR(40) NOT NULL, 
	sap_so_number VARCHAR(30), 
	sap_so_doc_entry INTEGER, 
	sap_so_line_num INTEGER, 
	sap_fg_item_code VARCHAR(80), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_job_detail_fg_header UNIQUE (detail_line_id, header_line_id), 
	FOREIGN KEY(job_id) REFERENCES job_master (job_no) ON DELETE CASCADE, 
	FOREIGN KEY(detail_line_id) REFERENCES job_detail_line (id) ON DELETE CASCADE, 
	FOREIGN KEY(header_line_id) REFERENCES job_header_line (id) ON DELETE CASCADE
)

;


CREATE TABLE bom_step (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	bom_id INTEGER NOT NULL, 
	seq_no INTEGER NOT NULL, 
	process_code VARCHAR(20) NOT NULL, 
	step_name VARCHAR(100) NOT NULL, 
	warehouse VARCHAR(20), 
	uom VARCHAR(10), 
	planned_qty NUMERIC(14, 4), 
	output_item_code VARCHAR(50), 
	sap_doc_entry INTEGER, 
	sap_doc_num INTEGER, 
	sap_warehouse VARCHAR(20), 
	production_order_remarks VARCHAR(254), 
	PRIMARY KEY (id), 
	CONSTRAINT uq_bom_step_seq UNIQUE (bom_id, seq_no), 
	FOREIGN KEY(bom_id) REFERENCES bom (id) ON DELETE CASCADE, 
	FOREIGN KEY(process_code) REFERENCES process_master (process_code) ON DELETE RESTRICT
)

;


CREATE TABLE bom_step_input (
	id INTEGER NOT NULL AUTO_INCREMENT, 
	bom_step_id INTEGER NOT NULL, 
	input_type ENUM('raw_material','consumable','labour') NOT NULL, 
	sap_item_code VARCHAR(50), 
	description VARCHAR(200), 
	uom VARCHAR(10), 
	qty_per_job NUMERIC(14, 4), 
	sap_warehouse VARCHAR(20), 
	PRIMARY KEY (id), 
	FOREIGN KEY(bom_step_id) REFERENCES bom_step (id) ON DELETE CASCADE
)

;

-- INDEXES

CREATE UNIQUE INDEX ix_process_master_process_code ON process_master (process_code);
CREATE INDEX ix_process_master_category ON process_master (category);
CREATE INDEX ix_sap_customer_mirror_synced_at ON sap_customer_mirror (synced_at);
CREATE INDEX ix_sap_item_mirror_item_type ON sap_item_mirror (item_type);
CREATE INDEX ix_sap_item_mirror_default_warehouse ON sap_item_mirror (default_warehouse);
CREATE INDEX ix_sap_item_mirror_synced_at ON sap_item_mirror (synced_at);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE INDEX ix_generated_fg_items_item_code ON generated_fg_items (item_code);
CREATE UNIQUE INDEX ix_job_cards_job_card_number ON job_cards (job_card_number);
CREATE INDEX ix_job_master_sap_customer_code ON job_master (sap_customer_code);
CREATE INDEX ix_job_master_sap_job_card_doc_entry ON job_master (sap_job_card_doc_entry);
CREATE INDEX ix_job_master_original_job_no ON job_master (original_job_no);
CREATE UNIQUE INDEX ix_job_master_job_no ON job_master (job_no);
CREATE INDEX ix_job_master_overall_status ON job_master (overall_status);
CREATE UNIQUE INDEX ix_roll_grn_entry_grn_number ON roll_grn_entry (grn_number);
CREATE INDEX ix_integration_event_job_id ON integration_event (job_id);
CREATE INDEX ix_integration_event_state ON integration_event (state);
CREATE UNIQUE INDEX ix_item_master_item_code ON item_master (item_code);
CREATE INDEX ix_item_master_parent_fg_code ON item_master (parent_fg_code);
CREATE INDEX ix_job_detail_line_job_id ON job_detail_line (job_id);
CREATE INDEX ix_job_header_line_job_id ON job_header_line (job_id);
CREATE INDEX ix_job_header_line_sap_fg_item_code ON job_header_line (sap_fg_item_code);
CREATE INDEX ix_job_status_history_job_id ON job_status_history (job_id);
CREATE INDEX ix_bom_is_active ON bom (is_active);
CREATE INDEX ix_bom_job_id ON bom (job_id);
CREATE INDEX ix_bom_detail_line_id ON bom (detail_line_id);
CREATE INDEX ix_job_detail_line_fg_involved_header_line_id ON job_detail_line_fg_involved (header_line_id);
CREATE INDEX ix_job_detail_line_fg_involved_detail_line_id ON job_detail_line_fg_involved (detail_line_id);
CREATE INDEX ix_job_detail_line_fg_involved_job_id ON job_detail_line_fg_involved (job_id);
CREATE INDEX ix_bom_step_bom_id ON bom_step (bom_id);
CREATE INDEX ix_bom_step_input_sap_item_code ON bom_step_input (sap_item_code);
CREATE INDEX ix_bom_step_input_bom_step_id ON bom_step_input (bom_step_id);

SET FOREIGN_KEY_CHECKS = 1;