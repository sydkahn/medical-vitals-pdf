from flask import Flask, request, send_file
import sqlite3
import io
import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.textlabels import Label
from reportlab.lib.units import inch
import os
import tempfile # Use tempfile for security

app = Flask(__name__)

DATABASE_PATH = '/root/medical-vitals-tracker/vitals.db'

def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def query_data(patient_initials=None, start_date=None, end_date=None):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT patient_initials, datetime_recorded, glucose, weight, temperature, systolic, diastolic, heart_rate, oxygen_saturation, notes
        FROM vital_records
        WHERE 1=1
    """
    params = []
    if patient_initials:
        query += " AND patient_initials = ?"
        params.append(patient_initials)
    if start_date:
        query += " AND datetime_recorded >= ?"
        params.append(start_date)
    if end_date:
        query += " AND datetime_recorded <= ?"
        params.append(end_date)

    query += " ORDER BY datetime_recorded ASC;"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def generate_chart_image(data, chart_type='glucose'):
    if not data:
        return None

    # Filter data based on chart_type
    dates = [row['datetime_recorded'] for row in data]
    values = [row[chart_type] for row in data if row[chart_type] is not None]
    # Corresponding dates for the filtered values
    value_dates = [d for d, v in zip(dates, [row[chart_type] for row in data]) if v is not None]

    if not values:
        return None

    plt.figure(figsize=(6, 4)) # Smaller figure for fitting multiple on a page
    plt.plot(value_dates, values, marker='o', linestyle='-', linewidth=1, markersize=3)
    plt.title(f'{chart_type.capitalize()}', fontsize=10)
    plt.xlabel('Date/Time', fontsize=8)
    plt.ylabel(chart_type.capitalize(), fontsize=8)
    plt.xticks(rotation=45, fontsize=6) # Rotate and reduce font size
    plt.yticks(fontsize=6)
    plt.grid(True, linestyle='--', alpha=0.6) # Add subtle grid
    plt.tight_layout(pad=0.5) # Tighter layout

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', dpi=150) # Higher DPI for better quality
    img_buffer.seek(0)
    plt.close()
    return img_buffer

def create_pdf_report(data, chart_images_dict, output_filename, initials_filter, start_date_filter, end_date_filter):
    doc = SimpleDocTemplate(output_filename, pagesize=letter)
    story = []
    styles = getSampleStyleSheet()
    style_normal = styles['Normal']

    # Store temporary file paths to delete later
    temp_files_to_delete = []

    # Add Title and Filters Summary
    story.append(Paragraph("Vital Statistics Report", styles['Heading1']))
    if initials_filter or start_date_filter or end_date_filter:
        filter_text = f"Filters: "
        if initials_filter:
            filter_text += f"Initials: {initials_filter}, "
        if start_date_filter:
            filter_text += f"Start: {start_date_filter}, "
        if end_date_filter:
            filter_text += f"End: {end_date_filter}"
        # Remove trailing comma and space if needed
        filter_text = filter_text.rstrip(', ')
        story.append(Paragraph(filter_text, style_normal))
    story.append(Spacer(1, 12))

    # Add Summary
    if data:
        story.append(Paragraph(f"Total Records: {len(data)}", style_normal))
        # Example summary calculation
        glucose_values = [r['glucose'] for r in data if r['glucose'] is not None]
        if glucose_values:
             avg_glucose = sum(glucose_values) / len(glucose_values)
             story.append(Paragraph(f"Avg Glucose: {avg_glucose:.2f}", style_normal))
    story.append(Spacer(1, 12))

    # Add Data Grid (Table) with Abbreviated Headers
    if data:
        # Define abbreviated headers
        header_mapping = {
            'patient_initials': 'Pt Init',
            'datetime_recorded': 'Date/Time',
            'glucose': 'Gluc',
            'weight': 'Wgt',
            'temperature': 'Temp',
            'systolic': 'Sys',
            'diastolic': 'Dia',
            'heart_rate': 'HR',
            'oxygen_saturation': 'SpO2',
            'notes': 'Notes'
        }
        headers = [header_mapping.get(key, key) for key in data[0].keys()] # Map keys to abbreviations
        table_data = [headers] + [[str(row[key]) for key in data[0].keys()] for row in data] # Use original keys for data lookup

        # Calculate column widths proportionally based on content or header length
        # Example: Define relative widths (total should be close to page width)
        # Pt Init, Date/Time, Gluc, Wgt, Temp, Sys, Dia, HR, SpO2, Notes
        proportions = [0.8, 1.5, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 1.2] # Adjust these
        total_proportion = sum(proportions)
        page_width = letter[0] - doc.leftMargin - doc.rightMargin
        col_widths = [p * (page_width / total_proportion) for p in proportions]

        data_table = Table(table_data, colWidths=col_widths) # Apply calculated widths
        data_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8), # Smaller font for headers
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6), # Less padding
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 6), # Smaller font for data
            ('GRID', (0, 0), (-1, -1), 0.5, colors.black) # Thinner grid lines
        ]))
        story.append(data_table)
    story.append(Spacer(1, 12))

    # Add Charts in a 2x2 grid per page (4 charts per page)
    chart_cells = []
    for chart_name, img_buffer in chart_images_dict.items():
        if img_buffer:
            temp_img_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            temp_img_file.write(img_buffer.getvalue())
            temp_img_file.close()
            temp_files_to_delete.append(temp_img_file.name)

            img_flowable = RLImage(temp_img_file.name, width=3.5*inch, height=2.5*inch)
            cell_contents = [Paragraph(f"{chart_name.capitalize()} Chart", styles['Heading4']), img_flowable]
            chart_cells.append(cell_contents)

    # Organize charts into rows of 2, with 4 charts per page
    for i in range(0, len(chart_cells), 4):
        page_group = chart_cells[i:i + 4]
        rows = []
        for row_index in range(0, 4, 2):
            row = []
            for col_index in range(2):
                cell_index = row_index + col_index
                if cell_index < len(page_group):
                    row.append(page_group[cell_index])
                else:
                    row.append('')
            rows.append(row)

        chart_table = Table(rows, colWidths=[3.75*inch, 3.75*inch])
        chart_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        story.append(chart_table)
        if i + 4 < len(chart_cells):
            story.append(PageBreak())

    # Build the document
    doc.build(story)

    # Delete temporary image files AFTER the document is built
    for temp_path in temp_files_to_delete:
        try:
            os.unlink(temp_path)
            print(f"Deleted temporary image file: {temp_path}")
        except OSError as e:
            print(f"Error deleting temporary file {temp_path}: {e}")


@app.route('/report/pdf')
def generate_pdf():
    patient_initials = request.args.get('initials')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    data = query_data(patient_initials, start_date, end_date)

    if not data:
        return "No data found for the specified filters.", 404

    # Generate chart images
    chart_types_to_include = ['glucose', 'weight', 'systolic', 'diastolic', 'heart_rate', 'oxygen_saturation']
    chart_images = {}
    for ct in chart_types_to_include:
        img_buf = generate_chart_image(data, ct)
        if img_buf:
            chart_images[ct] = img_buf

    # Create PDF using tempfile for security
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_file:
        output_path = tmp_file.name

    create_pdf_report(data, chart_images, output_path, patient_initials, start_date, end_date)

    # Serve the PDF file
    # Explicitly set the content type
    return send_file(output_path, as_attachment=True, download_name='vital_report.pdf'), 200, {'Content-Type': 'application/pdf'}

@app.route('/')
def index():
    return '<h1>Python Report Generator</h1><p>Use <code>/report/pdf?initials=X&amp;start_date=YYYY-MM-DD&amp;end_date=YYYY-MM-DD</code> to generate a report.</p>'

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081, debug=False)