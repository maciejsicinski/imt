import os
import re
import xml.etree.ElementTree as ET
import sqlglot
from sqlglot import exp
from sqlglot.dialects.bigquery import BigQuery
from sqlglot.dialects.teradata import Teradata
import datetime
import random
import subprocess 
import sys

prefix_vm = "/opt/imt/"

# Folder path to scan
run_sh_path = f"{prefix_vm}dwh-dumper-project/sql/run.sh"
folder_path = f"{prefix_vm}xml_metadata"
folder_path_wf = f"{prefix_vm}xml_metadata/wf"
teradata_metadata_path = f"{prefix_vm}teradata_metadata_extract"
valid_mappings_path = f"{prefix_vm}lookups/valid_mappings"

# Output directory path
translated_folder_path = f"{prefix_vm}sql_extract/bq_based_queries"
output_dir_path = f"{prefix_vm}sql_extract/teradata_based_queries"
alias_errors_dir_path = f"{prefix_vm}sql_extract/alias_errors"
column_errors_dir_path = f"{prefix_vm}sql_extract/column_errors"
prefix_errors_dir_path = f"{prefix_vm}sql_extract/prefix_errors"
xml_output_dir = f"{prefix_vm}xml_metadata_out"
xml_output_dir_wf = f"{prefix_vm}xml_metadata_out/wf"
table_names_errors_dir_path = f"{prefix_vm}sql_extract/errors_table_names"
td_output = f"{prefix_vm}dwh-dumper-project/sql/input"

# Create the output directory if it doesn't exist
os.makedirs(output_dir_path, exist_ok=True)
os.makedirs(alias_errors_dir_path, exist_ok=True)
os.makedirs(column_errors_dir_path, exist_ok=True)
os.makedirs(prefix_errors_dir_path, exist_ok=True)
os.makedirs(translated_folder_path, exist_ok=True)
os.makedirs(xml_output_dir, exist_ok=True)
os.makedirs(table_names_errors_dir_path, exist_ok=True)
os.makedirs(folder_path_wf, exist_ok=True)
os.makedirs(xml_output_dir_wf, exist_ok=True)

def removeFiles(folder_path):
    file_list = os.listdir(folder_path)

    # Loop through the files and delete them
    for file_name in file_list:
        file_path = os.path.join(folder_path, file_name)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)  # Delete files
            elif os.path.isdir(file_path):
                os.rmdir(file_path)   # Delete subdirectories (empty)
        except Exception as e:
            print(f"Error deleting {file_path}: {str(e)}")

def splitSQLQuery(sql_query):
    # Regular expression to match DECLARE blocks
    declare_pattern = r'DECLARE[^;]+;'
    declare_matches = re.findall(declare_pattern, sql_query, re.DOTALL)

    # Combine DECLARE blocks and the following fragment
    first_fragment = ""
    for i, declare_match in enumerate(declare_matches):
        first_fragment += declare_match
        if i < len(declare_matches) - 1:
            first_fragment += "\n"

    # Remove DECLARE blocks from the second fragment
    second_fragment = re.sub(declare_pattern, '', sql_query)

    return first_fragment.strip(), second_fragment.strip()

def processFileWf(filename, file_name):
    """
    This function parses through the XML files and modifies its content in order to generate informatica workflow compatible with BigQuery.


    For each ...
    Update XML file with modified values
    Write informatica header into a file
    Write modified XML into a out file

    Parameters
    ----------
    filename : str: 
        a path to the XML document (Informatica workflow)
    Returns
    -------
    bool
        True if successful, False otherwise.  

    """
    valid_mapping_names = set()
    with open(valid_mappings_path, "r") as valid_mappings_file:
        for line in valid_mappings_file:
            valid_mapping_names.add(line.strip())

    with open(filename, "r", encoding="iso-8859-1") as fh:
        doc = ET.parse(fh)
        root = doc.getroot()
        for folder in root.iter("FOLDER"):
            for mapping in folder.iter("MAPPING"):
                mapping_name = mapping.attrib["NAME"]   
                if mapping_name in valid_mapping_names:
                    if mapping_name not in mapping_total:
                        mapping_total[mapping_name] = True 
            for wf in folder.iter("WORKFLOW"):        
                wf_name = wf.attrib["NAME"]
                if wf_name not in wf_total:
                    wf_total[wf_name] = True 



        #folder -> workflow [get NAME] -> session [get MAPPINGNAME] -> SESSIONEXTENSION -> CONNECTIONREFERENCE [get CONNECTIONNAME and change it to the new one] 
        # check if CNXREFNAME = "DB Connection" update VARIABLE="$newvariable" or CONNECTIONNAME = new_connection_tobq
        #what about $source/target connection value
        filename_out = file_name + "_OUT.XML"
        file_path_out = os.path.join(xml_output_dir_wf, filename_out)
        with open(file_path_out, "w", encoding="iso-8859-1") as fh:
            fh.write("<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?>\n")
            fh.write("<!-- Informatica proprietary -->\n")
            fh.write("<!DOCTYPE POWERMART SYSTEM \"powrmart.dtd\">\n")
            fh.write(ET.tostring(root).decode("iso-8859-1"))

def removePrefixFromSQL(sql_query):
    """
    Removes the prefix(inserted by GCP bulk translator) from a sql query.

    Parameters
    ----------
    sql_query : (str)
        SQL query to be modified.
    Returns
    -------
    modified_query : (str)
        Query where the string_to_replace_from variable value was replaced by string_to_replace_to variable value                 
    """
    
    string_to_replace_from = "`gcp-ch-d-prj-i-edp`.dev_stg"
    string_to_replace_to = "dev_stg."

    modified_query = sql_query.replace(string_to_replace_from, string_to_replace_to)

    string_to_replace_from = ", 'ROUND_HALF_EVEN'"
    string_to_replace_to = ''

    modified_query = modified_query.replace(string_to_replace_from, string_to_replace_to)
   
    return modified_query

def isAProperQuery(sql_query):
    """
    Checks if the sql_query provided as a parameter contains lines that are not empty and are not comments.

    Parameters
    ----------
    sql_query : (str)
        SQL query to be validated.

    Returns
    -------
    bool
        True if successful, False otherwise.                    
    """
    lines = [line.strip() for line in sql_query.splitlines()]
    non_comment_lines = [line for line in lines if line and not line.startswith("--") and not line.startswith("/*")]
    if not non_comment_lines:
        return False
    else:
        return True

def addColumnAlias(sql_query, dialect):
    """
    Adds an alias to every expression in the sql query without alias.

    Parameters
    ----------
    sql_query : (str)
        SQL query to be modified.
    dialect : (sqlglot.dialect)
        A dialect to be used to parse a query (Bigquery or Teradata).
    Returns
    -------
    modified_query : (str)
        Query where every expression has an alias.               
    """

    parsed = sqlglot.parse(sql_query, dialect)[0]
    expressions = parsed.find(exp.Select).args["expressions"]
    # Find the expression corresponding to the column name
    j = 0
    for i, expression in enumerate(expressions):
        if expression and not expression.alias and hasattr(expression, '__dict__'):
            if isinstance(expression, exp.Column):  # Skip column names
                continue
            j+=1
            new_expression = f"{expression} AS expression_{i+1}" 
            parsed_expression = sqlglot.parse_one(new_expression).find(exp.Expression)
            expressions[i] = parsed_expression
    # Get the modified SQL query
    if j > 0:
        modified_query = str(parsed)
    else: 
        modified_query = sql_query
    return modified_query
    

def bulkTranslate():
    """
    Translated SQL queries from the input folder and save the output queries in a output folder.

    Uses Bulk Translate CLI from GCP.

    Returns
    -------
    bool
        True if successful, False otherwise.                    
    """
    #generate custom GCS prefix (not used)
    current_datetime = datetime.datetime.now()
    datetime_string = current_datetime.strftime("%Y%m%d%H%M%S")
    random_number = random.randint(0, 9999)
    result_string = f"{datetime_string}_{random_number:04d}"

    #modify the run.sh so it will take the generated prefix
    with open(run_sh_path, "r") as file:
        lines = file.readlines()
        for i, line in enumerate(lines):
            if "BQMS_GCS_PREFIX=" in line:
                # Update the line with the new value
                lines[i] = f'BQMS_GCS_PREFIX="{result_string}"\n'
                break

    with open(run_sh_path, "w") as file:
        file.writelines(lines)

    print("Generated new GCS prefix")

    # Set environment variables
    os.environ["BQMS_VERBOSE"] = "False"
    os.environ["BQMS_MULTITHREADED"] = "True"
    os.environ["BQMS_PROJECT"] = "gcp-ch-d-prj-i-edp"
    os.environ["BQMS_GCS_BUCKET"] = "translation_gcp_api/dev"

    print("Updated environment variables for translation")

    # Command to execute the shell script
    run_sh_command = f"{prefix_vm}dwh-dumper-project/sql/run.sh"

    print("Executing translation script")

    # Execute the shell script
    try:
        subprocess.run(run_sh_command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error while executing the shell script: {e}")
        exit(1)

    print("Shell script executed successfully.")

    # Command to execute the gsutil cp command
    print("Downloading translated queries")

    gsutil_command = f"gsutil -m cp -r gs://translation_gcp_api/dev/{result_string}/translated/* {prefix_vm}sql_extract/bq_based_queries"

    # Execute the gsutil command
    try:
        subprocess.run(gsutil_command, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error while executing the gsutil command: {e}")
        exit(1)

    print("Translated Files copied from GCS to local machine successfully.")

def saveSqlQueryToFile(folder_name, mapping_name, transformation_name, ttype, output_path, query):
    """
    Saves a SQL query to a file.

    The name of the file consists of specific XML components divided by "#". The structure is as follows
    folder_name#mapping_name#transformation_name#ttype.sql
    Parameters
    ----------
    folder_name : (str)
        String with the folder name.
    mapping_name : (str)
        String with the mapping name.
    transformation_name : (str)
        String with the transformation name.
    ttype : (str)
        String with the transformation type.
    output_path : (str)
        Path to the output directory.
    query : (str)
        SQL query to be saved.
    Returns
    -------
    bool
        True if successful, False otherwise.                    
    """
    output_file_name = f"{folder_name}#{mapping_name}#{transformation_name}#{ttype}.sql"
    output_file_name = output_file_name.replace(" ", "")
    output_file_path = os.path.join(output_path, output_file_name)
    with open(output_file_path, "w") as output_file:
        output_file.write(query + "\n")

def replaceLoadIdExp(transformation):
    """
    Modifies EXPRESSION attribute of the TRANSFORMFIELD element. 

    This function adds cast to decimal(5) to the the $$LOAD_ID variable in the expression

    Parameters
    ----------
    transformation : (Element)
        An element of the XML document.

    Returns
    -------
    bool
        True if successful, False otherwise.                    
    """
    if transformation.attrib["TYPE"] == "Expression":
        for field in transformation.iter("TRANSFORMFIELD"):
            expression = field.get("EXPRESSION")
            if expression is not None and expression == "$$LOAD_ID":
                field.attrib["EXPRESSION"] = "TO_DECIMAL($$LOAD_ID,5)"

def updateConnectors(mapping, transformation_name, map):
    """
    Modifies FROMFIELD and TOFIELD attribute of the CONNECTOR element. 

    This function updates FROMFIELD and TOFIELD of the CONNECTOR element to the values generated by the port alignment function

    Parameters
    ----------
    mapping : (Element)
        An element of the XML document
    transformation : (Element)
        An element of the XML document
    map : (dict)
        A dictionary containing mapping of original port values to new values
                    
    Returns
    -------
    bool
        True if successful, False otherwise.  
                    
    """
    for connector in mapping.iter("CONNECTOR"):
        if connector.attrib["FROMINSTANCE"] == transformation_name and connector.attrib["FROMFIELD"] in map.keys():
            old = connector.attrib["FROMFIELD"] #functionalize
            new = map[connector.attrib["FROMFIELD"]]['to']
            if old != new:
                connector.attrib["FROMFIELD"] = map[connector.attrib["FROMFIELD"]]['to']
        elif connector.attrib["TOINSTANCE"] == transformation_name and connector.attrib["TOFIELD"] in map.keys():
            old = connector.attrib["TOFIELD"] #reuse function
            new = map[connector.attrib["TOFIELD"]]['to']
            if old != new:
                connector.attrib["TOFIELD"] = map[connector.attrib["TOFIELD"]]['to']

def updateConflictingPortNames(mapping, transformation_name, new_port, port_names):
    """
    Modifies NAME of the ports identified as duplicates after the port alignment. 

    This function updates NAME of the TRANSFORMFIELD element to the values generated by adding the suffix with the index

    Parameters
    ----------
    mapping : (Element)
        An element of the XML document
    transformation : (Element)
        An element of the XML document
    new_port : (str)
        name of the port that needs to be changed
    port_names : ([str])
        An Array of the connected ports in order to skip unconnected ports
    map : (dict)
        A dictionary containing mapping of original port values to new values

    Returns
    -------
    updated_ports : ([str])
        An array of all updated ports
    conflict_update : (dict)
        A dictionary containing a map from original values to new values
                    
    """
    updated_ports = []
    conflict_update = {}
    for transformation in mapping.iter("TRANSFORMATION"):
        if transformation.attrib["NAME"] == transformation_name:
            for index, port in enumerate(transformation.iter("TRANSFORMFIELD")):
                if new_port.lower()  == port.attrib["NAME"].lower() :
                    updated_name = port.attrib["NAME"] + "__" + str(index)
                    if port.attrib["NAME"] in port_names:
                        updated_ports.append(updated_name)
                    conflict_update [updated_name] = {
                        "original" : new_port
                    }
                    port.attrib["NAME"] = updated_name
    return updated_ports, conflict_update

def getSqlQuery(transformation):
    """
    Returns a SQL query for a specific transofrmation. 

    Parameters
    ----------
    transformation : (Element): 
        An element of the XML document
    Returns
    -------
    sql_query : (str): 
        A string containing SQL query   
                    
    """
    for ta in transformation.iter("TABLEATTRIBUTE"):
        if ta.attrib["NAME"] == "Sql Query":
            sql_query= ta.attrib["VALUE"]
    return sql_query

def updateSqlQuery(transformation, query):
    """
    Updates a SQL query (based on supplied parameter) for a specific transformation. 

    Parameters
    ----------
    transformation : (Element): 
        An element of the XML document
    query : (str)
        A string containing SQL query  
    Returns
    -------
    bool
        True if successful, False otherwise.  
                    
    """
    for ta in transformation.iter("TABLEATTRIBUTE"):
        if ta.attrib["NAME"] == "Sql Query":
            ta.attrib["VALUE"] = query

def getConnectedFrom(mapping, transformation_name):
    """
    Retrieves and returns a list of the ports connected to the specific transformation. 

    Parameters
    ----------
    mapping : (Element)
        An element of the XML document
    transformation : (Element): 
        An element of the XML document
    Returns
    -------
    connected_from: [str]
        A list of ports connected to this transformation (only from the transformation to the next component).  
                    
    """
    connected_from = []
    for connector in mapping.iter("CONNECTOR"):
        if connector.attrib["FROMINSTANCE"] == transformation_name:
            connected_from.append(connector.attrib["FROMFIELD"])
    return connected_from

def findColumns(query, dialect):
    """
    Retrieves and returns a list of the columns from the supplied SQL query. 

    Parameters
    ----------
    query : (str)
        SQL query to be parsed.
    Returns
    -------
    column_names: [str]
        A list of columns from the supplied SQL query.
                    
    """
    column_names = []
    for expression in sqlglot.parse(query, dialect)[0].find(exp.Select).args["expressions"]:
        column_names.append(expression.alias_or_name)
    return column_names

def extractQueries(filename):
    """
    Retrieves, returns and save a sql query to a file.

    Parameters
    ----------
    filename : (str)
        The name of the file containing .
    Returns
    -------
    column_names: [str]
        A list of columns from the supplied SQL query.
                    
    """
    with open(filename, "r", encoding="iso-8859-1") as fh:
        doc = ET.parse(fh)
        root = doc.getroot()
        for folder in root.iter("FOLDER"):
            folder_name = folder.attrib["NAME"]
            for child in folder.iter("MAPPING"):
                mapping_name = child.attrib["NAME"]
                #if mapping_name in valid_mapping_names:
                for sq in child.iter("TRANSFORMATION"):
                    ttype = sq.attrib["TYPE"]
                    transformation_name = sq.attrib["NAME"]
                    if ttype == "Source Qualifier":
                        query = getSqlQuery(sq)
                        if query is not None and len(query) > 0:
                            saveSqlQueryToFile(folder_name, mapping_name, transformation_name, ttype, td_output, query)

def extractSqlQueryFromFile(folder, mapping, transformation, ttype):
    """

    
    The name of the file consists of specific XML components divided by "#". The structure is as follows
    folder_name#mapping_name#transformation_name#ttype.sql

    Yields
    ------
    err_code : int
        Non-zero value indicates error code, or zero on success.
    err_msg : str or None
        Human readable error message, or None on success.
    """
    filename = folder + "#" + mapping + "#" + transformation + "#" + ttype + ".sql"
    filename = filename.replace(" ", "")
    file_path = os.path.join(translated_folder_path, filename)
    try:
        with open(file_path, "r", encoding="iso-8859-1") as fh:
            input_query = fh.read()
            return input_query
    except Exception as e:
        return e


def processFile(filename, file_name):
    """
    This function parses through the XML files and modifies its content in order to generate informatica mapping compatible with BigQuery.


    For each transformation get only Sql Query attribute to parse and save columns returned by a SQL query   
    For each transformation in each mapping get only Source Qualifier transformation and save it's port
    Only manipulate ports if they are connected (port is used further down the mapping)
    Don't update if the SQL parser returned an error
    create file with the mapping.sq so we know where the error occured
    Don't update if both lists contains the same values (lowercased)
    Update NAME attribute values 
    Update XML file with modified values
    Write informatica header into a file
    Write modified XML into a out file

    Parameters
    ----------
    filename : str: 
        a path to the XML document (Informatica mapping)
    Returns
    -------
    bool
        True if successful, False otherwise.  

    """

    valid_mapping_names = set()
    with open(valid_mappings_path, "r") as valid_mappings_file:
        for line in valid_mappings_file:
            valid_mapping_names.add(line.strip())

    with open(filename, "r", encoding="iso-8859-1") as fh:
        j = 0
        doc = ET.parse(fh)
        root = doc.getroot()
        for folder in root.iter("FOLDER"):
            folder_name = folder.attrib["NAME"]
            for child in folder.iter("MAPPING"):
                mapping_name = child.attrib["NAME"]    
                if mapping_name in valid_mapping_names:              
                    if mapping_name not in mapping_total:
                        mapping_total[mapping_name] = True 
                    for sq in child.iter("TRANSFORMATION"):                
                        replaceLoadIdExp(sq)
                        field_dict = {} 
                        conflict_update = {}
                        ttype = sq.attrib["TYPE"]
                        transformation_name = sq.attrib["NAME"]
                        connected_from = getConnectedFrom(child, transformation_name)
                        if ttype == "Source Qualifier":
                            query = getSqlQuery(sq)
                        if ttype == "Source Qualifier" and query is not None and len(query) > 0:     
                            dialect = BigQuery
                            translated_query = extractSqlQueryFromFile(folder_name, mapping_name, transformation_name, ttype)  
                            if isinstance(translated_query, Exception) or not isAProperQuery(translated_query):
                                if mapping_name not in mapping_errors:
                                    mapping_errors[mapping_name] = True 
                                continue     
                            if not isinstance(translated_query, Exception) and isAProperQuery(translated_query):
                                dialect = BigQuery
                                try:
                                    translated_query = removePrefixFromSQL(translated_query)
                                except Exception as e:
                                    print(e)
                                    j+=1
                                    saveSqlQueryToFile(folder_name, mapping_name, transformation_name, ttype, prefix_errors_dir_path, str(e)) 
                                    if mapping_name not in mapping_errors:
                                        mapping_errors[mapping_name] = True 
                                updateSqlQuery(sq, translated_query)
                                query = getSqlQuery(sq)
                            port_names = []
                            field_nodes = list(sq.iter("TRANSFORMFIELD"))
                            for field in field_nodes:
                                    if field.attrib["NAME"] in connected_from:
                                        port_names.append(field.attrib["NAME"])
                            try:
                                declare_block, main_query = splitSQLQuery(query)
                                new_query = addColumnAlias(main_query, dialect)
                                query = "\n".join([declare_block, new_query])
                            except Exception as e:
                                j+=1
                                saveSqlQueryToFile(folder_name, mapping_name, transformation_name, ttype, alias_errors_dir_path, str(e))
                                if mapping_name not in mapping_errors:
                                    mapping_errors[mapping_name] = True      
                            try:      
                                sql_columns = findColumns(new_query, dialect)                 
                            except Exception as e:
                                saveSqlQueryToFile(folder_name, mapping_name, transformation_name, ttype, column_errors_dir_path, str(e))
                                j += 1
                                if mapping_name not in mapping_errors:
                                    mapping_errors[mapping_name] = True 
                                continue
                            else:
                                i = 0
                                for field in sq.iter("TRANSFORMFIELD"):
                                    if i < len(sql_columns) and field.attrib["NAME"] in port_names:
                                        if sql_columns[i].lower() != field.attrib["NAME"].lower():    
                                            field_dict[field.attrib["NAME"]]= {  
                                            "from": field.attrib["NAME"],
                                            "to" : sql_columns[i]
                                            }
                                            next = sql_columns[i]
                                            updated_ports, conflict_update_tmp = updateConflictingPortNames(
                                                mapping=child,
                                                transformation_name=transformation_name,
                                                new_port=next,
                                                port_names = port_names
                                            )
                                            if conflict_update_tmp:
                                                conflict_update.update(conflict_update_tmp)
                                            for item in updated_ports:
                                                port_names.append(item)
                                            field.attrib["NAME"] = sql_columns[i] 
                                        i+=1
                                field_dict_2 = {}
                                if(conflict_update):
                                    for key, value in field_dict.items():
                                        if key in conflict_update:
                                            original_key = conflict_update[key]['original']
                                            field_dict_2[original_key] = value
                                            field_dict_2[original_key]['from'] = original_key
                                        else:
                                            field_dict_2[key] = value
                                    for key, value in conflict_update.items():
                                        if key not in field_dict.keys() :
                                            field_dict_2 [conflict_update[key]['original']]= {  
                                            "from": conflict_update[key]['original'],
                                            "to" : key
                                            }
                                else:
                                    field_dict_2 = field_dict
                                updateConnectors(child, transformation_name, field_dict_2)
        filename_out = file_name + "_OUT.XML"
        file_path_out = os.path.join(xml_output_dir, filename_out)
        with open(file_path_out, "w", encoding="iso-8859-1") as fh:
            fh.write("<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?>\n")
            fh.write("<!-- Informatica proprietary -->\n")
            fh.write("<!DOCTYPE POWERMART SYSTEM \"powrmart.dtd\">\n")
            fh.write(ET.tostring(root).decode("iso-8859-1"))
        return j

def processWorkflows():
    print("Processing workflows")
    for filename_wf in os.listdir(folder_path_wf):
        if filename_wf.endswith(".XML"):
            file_path_wf = os.path.join(folder_path_wf, filename_wf)
            a = processFileWf(file_path_wf, filename_wf)
            #i+=a
    #print(f"number of parsing errors in workflows: {i}")
    print("processed")

def extractingQueries():
    print("Extracting TD queries")
    for filename in os.listdir(folder_path):
        if filename.endswith(".XML"):
            file_path = os.path.join(folder_path, filename)
            extractQueries(file_path)
    print("Queries extracted")

def processMappings():
    i = 0
    # Process each file in the folder to parse and update the XML's
    print("running main parser")
    for filename in os.listdir(folder_path_wf):
        if filename.endswith(".XML"):
            file_path = os.path.join(folder_path_wf, filename)
            a = processFile(file_path, filename)
            i+=a
    print(f"number of parsing errors: {i}")


# Main program                                    
# Process each file in the folder and save each sql query to a separate file
if len(sys.argv) != 2:
    print("Usage: python script.py [a/m/w]")
    sys.exit()
option = sys.argv[1]
mapping_errors = {} 
wf_errors = {} 
mapping_total = {} 
wf_total = {} 
error_count = 0

removeFiles(alias_errors_dir_path)
removeFiles(prefix_errors_dir_path)
removeFiles(column_errors_dir_path)
removeFiles(table_names_errors_dir_path)

if option == "a":
    processWorkflows()
    extractingQueries()
    # Setup GCP Bulk Translate API and translate the queries
    bulkTranslate()
    processMappings()
elif option == "m":
    processMappings()
elif option == "w":
    processWorkflows()
else:
    print("Invalid option. Use 'a' for the whole program including extraction of the queries and translation API, 'm' for processing mappings only, or 'w' for processing workflows only.")

error = sum(mapping_errors.values())
total_map = sum(mapping_total.values())
total_wf = sum(wf_total.values())

print(f"number of affected mappings: {error}")
print(f"number of mappings: {total_map}")

print(f"number of wf: {total_wf}")
print(f"number of wf errors: {wf_errors}")