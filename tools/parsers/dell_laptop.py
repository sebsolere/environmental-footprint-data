"""Parsers for Dell LCA PDF.

See an example here https://i.dell.com/sites/csdocuments/CorpComm_Docs/en/carbon-footprint-wyse-3030.pdf
"""

import logging
import re
import datetime
from typing import BinaryIO, Iterator

from tools.parsers.lib import data
from tools.parsers.lib.image import crop, find_text_in_image, image_to_text
from tools.parsers.lib import loader
from tools.parsers.lib import pdf
from tools.parsers.lib import text


# A list of patterns to search in the text.
_DELL_LCA_PATTERNS = (
    re.compile(r' (?P<name>.*?)\s*From design to end-of-life'),
    re.compile(r' estimated carbon footprint\: \s*(?P<footprint>[0-9]*) kgCO2e(?: \+\/\- (?P<error>[0-9]*) kgCO2e)?'),
    re.compile(r' estimated standard deviation of \+\/\- (?P<error>[0-9]*)\s*kgCO2e'),
    re.compile(r' Report produced\s*(?P<date>[A-Z][a-z]*,* [0-9]{4}) '),
    re.compile(r' Product Weight\s*(?P<weight>[0-9]*.[0-9]*)\s*kg'),
    re.compile(r' Screen Size\s*(?P<screen_size>[0-9]*)'),
    re.compile(r'Assembly Location\s*(?P<assembly_location>[A-Za-z]*)\s+'),
    re.compile(r'Product Lifetime\s*(?P<lifetime>[0-9]*) years'),
    re.compile(r' Use Location\s*(?P<use_location>[A-Za-z]*)\s+'),
    re.compile(r' Energy Demand \(Yearly TEC\)\s*(?P<energy_demand>[0-9]*.[0-9]*)\s*kWh'),
    re.compile(r' HDD\/SSD Quantity (?P<hdd>.*(?:SSD|HDD?))\s+'),
    re.compile(r' DRAM Capacity\s*(?P<ram>[0-9]*)[A-Z]{2}\s+'),
    re.compile(r' CPU Quantity\s*(?P<cpu>[0-9]*)\s+'),
    re.compile(r'Use\s*(?P<gwp_use_ratio>[0-9]*\.*[0-9]*)%'),
    re.compile(r'Manufacturing\s*(?P<gwp_manufacturing_ratio>[0-9]*\.*[0-9]*)%'),
    re.compile(r'EoL\s*(?P<gwp_eol_ratio>[0-9]*\.*[0-9]*)%'),
    re.compile(r'Transportation\s*(?P<gwp_transport_ratio>[0-9]*\.*[0-9]*)%')
)

_CATEGORIES = {
    'Monitor': ('Workplace', 'Monitor'),
    'Poweredge': ('Datacenter', 'Server'),
    'Latitude': ('Workplace', 'Laptop'),
    'OptiPlex': ('Workplace', 'Desktop'),
    'Precision': ('Workplace', 'Desktop'),
    'Wyse': ('Workplace', 'Thin client'),
    'XPS': ('Workplace', 'Laptop'),
}

_USE_PERCENT_PATTERN = re.compile(r'.*Use([0-9]*\.*[0-9]*)\%.*')
_MANUF_PERCENT_PATTERN = re.compile(r'.*Manufac(?:turing|uring|ture)([0-9]*\.*[0-9]*)\%.*')


def parse(body: BinaryIO, pdf_filename: str) -> Iterator[data.DeviceCarbonFootprint]:
    result = data.DeviceCarbonFootprintData()
    
    # Parse text from PDF.
    pdf_as_text = pdf.pdf2txt(body)
    extracted = text.search_all_patterns(_DELL_LCA_PATTERNS, pdf_as_text)
    if not extracted:
        logging.error('The file "%s" did not match the Dell pattern', pdf_filename)
        return

    # Convert each matched group to our format.
    if 'name' in extracted:
        result['name'] = extracted['name'].strip().removeprefix('Dell ')
    else:
        raise ValueError(pdf_as_text)
    for keyword, category_and_sub in _CATEGORIES.items():
        if keyword in result['name']:
            result['category'], result['subcategory'] = category_and_sub
            break
    if 'footprint' in extracted:
        result['gwp_total'] = float(extracted['footprint'])
    if result.get('gwp_total') and 'error' in extracted:
        result['gwp_error_ratio'] = round((float(extracted['error']) / result['gwp_total']), 4)
    else:
        raise ValueError(pdf_as_text)
    if 'date' in extracted:
        result['report_date'] = extracted['date']
    if 'weight' in extracted:
        result['weight'] = float(extracted['weight'])
    if 'screen_size' in extracted:
        result['screen_size'] = float(extracted['screen_size'])
    if 'assembly_location' in extracted:
        result['assembly_location'] = extracted['assembly_location']
    if 'lifetime' in extracted:
        result['lifetime'] = float(extracted['lifetime'])
    if 'use_location' in extracted:
        result['use_location'] = extracted['use_location']
    if 'energy_demand' in extracted:
        result['yearly_tec'] = float(extracted['energy_demand'])
    if 'hdd' in extracted:
        result['hard_drive'] = extracted['hdd']
    if 'ram' in extracted:
        result['memory'] = float(extracted['ram'])
    if 'cpu' in extracted:
        result['number_cpu'] = int(extracted['cpu'])
    if 'gwp_manufacturing_ratio' in extracted:
        result['gwp_manufacturing_ratio'] = float(extracted['gwp_manufacturing_ratio'])/100
    if 'gwp_use_ratio' in extracted:
        result['gwp_use_ratio'] = float(extracted['gwp_use_ratio'])/100
    if 'gwp_eol_ratio' in extracted:
        result['gwp_eol_ratio'] = float(extracted['gwp_eol_ratio'])/100
    if 'gwp_transport_ratio' in extracted:
        result['gwp_transport_ratio'] = float(extracted['gwp_transport_ratio'])/100  
    now = datetime.datetime.now()
    result['added_date'] = now.strftime('%Y-%m-%d')
    result['add_method'] = "Dell Auto Parser"

    if not 'gwp_use_ratio' in extracted:
        for image in pdf.list_images(body):
            # Search "Use x%" in the left part of the graph.
            cropped_left = crop(image, right=.75)
            use_block = find_text_in_image(cropped_left, re.compile('Use'), threshold=150)
            if use_block:
                # Create an image a bit larger, especially below the text found where the number is.
                use_image = cropped_left[
                    use_block.top - 3:use_block.top + use_block.height * 3,
                    use_block.left - 20:use_block.left + use_block.width + 20,
                ]
                use_text = image_to_text(use_image, threshold=130)
                clean_text = use_text.replace('\n', '').replace(' ', '')
                match_use = _USE_PERCENT_PATTERN.match(clean_text)
                if match_use:
                    result['gwp_use_ratio'] = float(match_use.group(1))/100

            # Search "Manufact... x%" in the middle part of the graph.
            cropped_right = crop(image, left=.25, right=.3)
            manuf_block = find_text_in_image(cropped_right, re.compile('Manufa'), threshold=50)
            if manuf_block:
                # Create an image a bit larger, especially below the text found where the number is.
                manuf_image = cropped_right[
                    manuf_block.top - 3:manuf_block.top + manuf_block.height * 3,
                    manuf_block.left - 8:manuf_block.left + manuf_block.width + 3,
                ]
                manuf_text = image_to_text(manuf_image, threshold=30)
                clean_text = manuf_text.replace('\n', '').replace(' ', '')
                match_use = _MANUF_PERCENT_PATTERN.match(clean_text)
                if match_use:
                    result['gwp_manufacturing_ratio'] = float(match_use.group(1))/100

            if manuf_block or use_block:
                break

    yield data.DeviceCarbonFootprint(result)


# Convenient way to run this scraper as a standalone.
if __name__ == '__main__':
    loader.main(parse)
