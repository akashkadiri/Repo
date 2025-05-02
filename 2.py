#!/usr/bin/env python3
"""
Git Repository Branch Comparison Tool

This script downloads a Git repository and compares files between branches.
It uses Git's built-in diff functionality without checking out branches.

Usage:
    python git_compare.py
"""

import os
import sys
import tempfile
import shutil
import logging
import json
import time
from subprocess import Popen, PIPE, TimeoutExpired
from urllib.parse import urlparse

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Constants
DEFAULT_TIMEOUT = 300  # 5 minutes timeout for Git operations
DEFAULT_MAIN_BRANCHES = ["master", "main"]


class GitComparisonTool:
    def __init__(self, repo_url, master_branch=None, feature_branch=None, auth=None, timeout=DEFAULT_TIMEOUT):
        self.repo_url = repo_url
        self.master_branch = master_branch
        self.feature_branch = feature_branch
        self.auth = auth
        self.timeout = timeout
        self.temp_dir = None
        
        # Validate inputs
        if not repo_url:
            raise ValueError("Repository URL is required")
        if not feature_branch:
            raise ValueError("Feature branch name is required")
            
    def run(self):
        """Run the complete comparison process and return results."""
        try:
            # Create temporary directory
            self.temp_dir = tempfile.mkdtemp(prefix="git-compare-")
            logger.info(f"Created temporary directory: {self.temp_dir}")
            
            # Clone repository as bare repo (no working directory)
            repo_path = self._clone_repository()
            
            # Get available branches
            branches = self._get_branches(repo_path)
            
            # Check if feature branch exists
            if self.feature_branch not in branches:
                raise ValueError(f"Feature branch '{self.feature_branch}' not found. Available branches: {', '.join(branches)}")
            
            # Determine main branch if not specified
            main_branch = self.master_branch if self.master_branch in branches else next((b for b in DEFAULT_MAIN_BRANCHES if b in branches), None)
            if not main_branch:
                raise ValueError(f"Could not determine main branch. Available branches: {', '.join(branches)}")
            
            logger.info(f"Comparing branches: {main_branch} and {self.feature_branch}")
            
            # Compare branches
            return self._compare_branches(repo_path, main_branch, self.feature_branch)
        finally:
            # Always clean up
            self._cleanup()

    def _run_git_command(self, command, cwd=None):
        """Run a Git command and return the result."""
        env = os.environ.copy()
        
        # Add auth environment if provided
        if self.auth:
            try:
                username, token = self.auth.split(':', 1)
                env.update({
                    "GIT_ASKPASS": "echo",
                    "GIT_USERNAME": username,
                    "GIT_PASSWORD": token
                })
            except ValueError:
                logger.warning("Invalid authentication format. Expected 'username:token'")
        
        # Log command (hiding sensitive info)
        safe_command = ' '.join(command)
        logger.debug(f"Running: {safe_command}")
        
        try:
            process = Popen(command, stdout=PIPE, stderr=PIPE, cwd=cwd, env=env, text=True)
            stdout, stderr = process.communicate(timeout=self.timeout)
            
            if process.returncode != 0:
                error_msg = stderr.strip()
                # Provide helpful error messages
                if "Authentication failed" in error_msg or "could not read Username" in error_msg:
                    raise ValueError(f"Authentication failed. Use username:token format for private repositories.")
                elif "repository not found" in error_msg:
                    raise ValueError(f"Repository not found: {self.repo_url}")
                else:
                    raise ValueError(f"Git error: {error_msg}")
            
            return stdout.strip()
            
        except TimeoutExpired:
            process.kill()
            raise ValueError(f"Command timed out after {self.timeout} seconds")
    
    def _clone_repository(self):
        """Clone the repository to temporary directory."""
        repo_path = os.path.join(self.temp_dir, "repo")
        self._run_git_command(["git", "clone", "--bare", self.repo_url, repo_path])
        logger.info(f"Successfully cloned repository")
        return repo_path
    
    def _get_branches(self, repo_path):
        """Get list of available branches."""
        self._run_git_command(["git", "fetch", "--all"], cwd=repo_path)
        branch_output = self._run_git_command(["git", "branch", "-r"], cwd=repo_path)
        
        branches = []
        for line in branch_output.splitlines():
            line = line.strip()
            if "->" not in line:  # Skip HEAD pointer
                branch = line.split("/", 1)[1] if "/" in line else line
                branches.append(branch)
        
        logger.info(f"Available branches: {', '.join(branches)}")
        return branches
    
    def _compare_branches(self, repo_path, base_branch, compare_branch):
        """Compare two branches using Git's diff."""
        changes = []
        
        # Get list of changed files with status
        diff_output = self._run_git_command(
            ["git", "diff", "--name-status", f"{base_branch}..{compare_branch}"],
            cwd=repo_path
        )
        
        # Process each line of diff output
        for line in diff_output.splitlines():
            if not line.strip():
                continue
                
            parts = line.split('\t')
            if len(parts) < 2:
                continue
                
            change_code = parts[0][0]  # Take first letter
            filename = parts[-1]  # Last part is the filename
            
            # Map change codes to types
            change_type_map = {
                'A': "added",
                'M': "modified",
                'D': "deleted",
                'R': "modified",  # Consider renames as modifications
                'C': "modified",  # Consider copies as modifications
            }
            
            change_type = change_type_map.get(change_code, "unchanged")
            changes.append({"filename": filename, "changeType": change_type})
        
        # Get unchanged files
        base_files = set(self._run_git_command(
            ["git", "ls-tree", "-r", "--name-only", base_branch],
            cwd=repo_path
        ).splitlines())
        
        compare_files = set(self._run_git_command(
            ["git", "ls-tree", "-r", "--name-only", compare_branch],
            cwd=repo_path
        ).splitlines())
        
        # Find changed file names for quick lookup
        changed_files = {item["filename"] for item in changes}
        
        # Add unchanged files
        common_files = base_files.intersection(compare_files)
        for file in common_files:
            if file not in changed_files:
                changes.append({"filename": file, "changeType": "unchanged"})
        
        # Log summary
        counts = {
            "added": sum(1 for c in changes if c["changeType"] == "added"),
            "modified": sum(1 for c in changes if c["changeType"] == "modified"),
            "deleted": sum(1 for c in changes if c["changeType"] == "deleted"),
            "unchanged": sum(1 for c in changes if c["changeType"] == "unchanged")
        }
        
        logger.info(
            f"Comparison complete - added: {counts['added']}, modified: {counts['modified']}, "
            f"deleted: {counts['deleted']}, unchanged: {counts['unchanged']}"
        )
        
        return changes
    
    def _cleanup(self):
        """Clean up temporary directory."""
        if self.temp_dir and os.path.exists(self.temp_dir):
            logger.info(f"Cleaning up temporary directory")
            try:
                shutil.rmtree(self.temp_dir)
            except Exception as e:
                logger.warning(f"Failed to clean up: {e}")


def main():
    """Main function to run the script."""
    print("\n=== Git Repository Branch Comparison Tool ===")
    
    # Get user input
    repo_url = input("\nEnter the Git repository URL: ").strip()
    master_branch = input("\nEnter the master/main branch name (leave empty to auto-detect): ").strip() or None
    feature_branch = input("\nEnter the feature branch name: ").strip()
    auth = input("\nEnter authentication if needed (username:token, leave empty if not required): ").strip() or None
    output_file = input("\nEnter output file path (leave empty to print to console): ").strip() or None
    
    try:
        # Create and run the comparison tool
        tool = GitComparisonTool(repo_url, master_branch, feature_branch, auth)
        changes = tool.run()
        
        # Output results
        if output_file:
            with open(output_file, 'w') as f:
                json.dump(changes, f, indent=2)
            print(f"\nResults written to {output_file}")
        else:
            print("\n=== Comparison Results ===")
            print(json.dumps(changes, indent=2))
            
    except Exception as e:
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
