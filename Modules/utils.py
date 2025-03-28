# Standard Python Libraries
from pathlib import Path
from typing import Optional, Literal, Union, Any


class InvalidBooleanValueError(Exception):
    pass

class InvalidNoneTypeValueError(Exception):
    pass


class Version:
    def __init__(self, version: str):
        from datetime import datetime
        from Modules.constants.standard import RE_VERSION_TIME

        version = version.strip()

        self.major, self.minor, self.patch = map(int, version[1:6:2])
        self.date = datetime.strptime(version[9:19], "%d/%m/%Y").date().strftime("%d/%m/%Y")

        # Check if the version string contains the time component
        if (
            len(version) == 27
            and RE_VERSION_TIME.search(version)
        ):
            self.time = datetime.strptime(version[21:26], "%H:%M").time().strftime("%H:%M")
            self.date_time = datetime.strptime(version[9:27], "%d/%m/%Y (%H:%M)")
        else:
            self.time = None
            self.date_time = datetime.strptime(version[9:19], "%d/%m/%Y")

    def __str__(self):
        return f"v{self.major}.{self.minor}.{self.patch} - {self.date}{f' ({self.time})' if self.time else ''}"


def get_documents_folder(use_alternative_method = False):
    """
    Retrieves the Path object to the current user's \"Documents\" folder by querying the Windows registry.

    Args:
        use_alternative_method: If set to `True`, the alternative method will be used to retrieve the "Documents" folder.\n
        If set to `False` (default), the registry-based method will be used.

    Returns:
        Path: A `Path` object pointing to the user's "Documents" folder.

    Raises:
        TypeError: If the retrieved path is not a string.
    """
    if use_alternative_method:
        # Alternative method using SHGetKnownFolderPath from WinAPI
        from win32com.shell import shell, shellcon # type:ignore # Seems like we can also use `win32comext.shell`

        # Get the Documents folder path
        documents_path = shell.SHGetKnownFolderPath(shellcon.FOLDERID_Documents, 0)
    else:
        # Default method using Windows registry
        import winreg
        from Modules.constants.standalone import USER_SHELL_FOLDERS__REG_KEY

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_SHELL_FOLDERS__REG_KEY) as key:
            documents_path, _ = winreg.QueryValueEx(key, "Personal")

    if not isinstance(documents_path, str):
        raise TypeError(f'Expected "str", got "{type(documents_path).__name__}"')

    return Path(documents_path)

def resource_path(relative_path: Path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    import sys

    base_path = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent) # .parent twice because of modularizing bruh
    if not isinstance(base_path, Path):
        base_path = Path(base_path)
    return base_path / relative_path

def take(n: int, input_list: list[Any]):
    """Return first n items from the given input list."""
    return input_list[:n]

def concat_lists_no_duplicates(*lists: list[Any]):
    """
    Concatenates multiple lists while removing duplicates and preserving order.

    Args:
        *lists: One or more lists to concatenate.
    """
    unique_list: list[Any] = []
    seen = set()

    for lst in lists:
        for item in lst:
            if item not in seen:
                unique_list.append(item)
                seen.add(item)

    return unique_list

def get_pid_by_path(filepath: Path):
    import psutil

    for process in psutil.process_iter(["pid", "exe"]):
        if process.info["exe"] == str(filepath.absolute()):
            return process.pid
    return None

def is_file_need_newline_ending(file: Union[str, Path]):
    if isinstance(file, Path):
        file_path = file
    else:
        file_path = Path(file)

    if file_path.stat().st_size == 0:
        return False

    return not file.read_bytes().endswith(b"\n")

def write_lines_to_file(file: Path, mode: Literal["w", "x", "a"], lines: list[str]):
    """
    Writes or appends a list of lines to a file, ensuring proper newline handling.

    Args:
        file: The path to the file.
        mode: The file mode ('w', 'x' or 'a').
        lines: A list of lines to write to the file.
    """
    # Copy the input lines to avoid modifying the original list
    content = lines[:]

    # If the content list is empty, exit early without writing to the file
    if not content:
        return

    # If appending to a file, ensure a leading newline is added if the file exists, otherwise creates it.
    if mode == "a":
        if file.is_file():
            if is_file_need_newline_ending(file):
                content.insert(0, "")
        else:
            file.touch()

    # Ensure the last line ends with a newline character
    if not content[-1].endswith("\n"):
        content[-1] += "\n"

    # Write content to the file
    with file.open(mode, encoding="utf-8") as f:
        f.writelines(content)

def terminate_process_tree(pid: int = None):
    """Terminates the process with the given PID and all its child processes.
       Defaults to the current process if no PID is specified."""
    import psutil

    pid = pid or psutil.Process().pid

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        psutil.wait_procs(children, timeout=5)
        try:
            parent.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        parent.wait(5)
    except psutil.NoSuchProcess:
        pass

def check_case_insensitive_and_exact_match(input_value: str, custom_values_list: list[str]):
    """
    Checks if the input value matches any string in the list case-insensitively, and whether it also matches exactly (case-sensitive).

    It also returns the correctly capitalized version of the matched value from the list if a case-insensitive match is found.

    Returns a tuple of three values:
    - The first boolean is True if a case-insensitive match is found.
    - The second boolean is True if the exact case-sensitive match is found.
    - The third value is the correctly capitalized version of the matched string if found, otherwise None.
    """
    case_insensitive_match = False
    case_sensitive_match = False
    normalized_match = None

    lowered_input_value = input_value.lower()
    for value in custom_values_list:
        if value.lower() == lowered_input_value:
            case_insensitive_match = True
            normalized_match = value
            if value == input_value:
                case_sensitive_match = True
                break

    return case_insensitive_match, case_sensitive_match, normalized_match

def custom_str_to_bool(string: str, only_match_against: Optional[bool] = None):
    """
    This function returns the boolean value represented by the string for lowercase or any case variation;\n
    otherwise, it raises an \"InvalidBooleanValueError\".

    Args:
        string: The boolean string to be checked.
        only_match_against (optional): If provided, the only boolean value to match against.
    """
    need_rewrite_current_setting = False
    resolved_value = None

    string_lower = string.lower()

    if string_lower == "true":
        resolved_value = True
    elif string_lower == "false":
        resolved_value = False

    if resolved_value is None:
        raise InvalidBooleanValueError("Input is not a valid boolean value")

    if (
        only_match_against is not None
        and only_match_against is not resolved_value
    ):
        raise InvalidBooleanValueError("Input does not match the specified boolean value")

    if not string == str(resolved_value):
        need_rewrite_current_setting = True

    return resolved_value, need_rewrite_current_setting

def custom_str_to_nonetype(string: str):
    """
    This function returns the NoneType value represented by the string for lowercase or any case variation; otherwise, it raises an \"InvalidNoneTypeValueError\".

    Args:
        string: The NoneType string to be checked.
    """
    if not string.lower() == "none":
        raise InvalidNoneTypeValueError("Input is not a valid NoneType value")

    is_string_literal_none = string == "None"
    return None, is_string_literal_none
