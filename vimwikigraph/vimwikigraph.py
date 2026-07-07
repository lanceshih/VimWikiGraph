import copy
import os
import re
import traceback
from logging import critical, debug, error, info, warning

import networkx as nx
import numpy as np


class VimwikiGraph:

    def __init__(self, root_dir: str, file_extensions: list, **args):
        self.graph = nx.DiGraph()
        self.root_dir = root_dir
        self.file_extensions = file_extensions
        self.lines = dict()
        self._canonical = dict()
        node_dict = self.__create_nodes()
        self.__parse_and_add_edges(node_dict)
        self.original_graph = copy.deepcopy(self.graph)

    def __create_nodes(self):
        node_dict = dict()
        for root, dirs, files in os.walk(self.root_dir):
            for file in files:
                split = file.split('.')
                if split[-1] in self.file_extensions:
                    full_path = os.path.join(root, file)
                    node_dict[full_path] = root
                    self.graph.add_node(full_path, label='.'.join(split[:-1]))
                    canonical = os.path.splitext(os.path.relpath(full_path, self.root_dir))[0]
                    self._canonical[canonical] = full_path
        return node_dict

    def __resolve_link(self, source_dir, link):
        if re.match(r'https?://', link):
            link = re.sub(r'.*:/+', '', link)
            return re.sub(r'/.*', '', link)
        if re.match(r'file:/', link):
            link = re.sub(r'.*:/+', '', link)
            return re.sub(r'.*/', '', link)
        link = link.split('#')[0]
        path = os.path.normpath(os.path.join(source_dir, link))
        canonical = os.path.splitext(os.path.relpath(path, self.root_dir))[0]
        return self._canonical.get(canonical, path)

    def __lookup_node(self, path):
        if path in self.graph.nodes:
            return path
        if os.path.isabs(path):
            canonical = os.path.splitext(os.path.relpath(path, self.root_dir))[0]
        else:
            canonical = os.path.splitext(path)[0]
        return self._canonical.get(canonical, path)

    def __parse_and_add_edges(self, node_dict):
        for name, root in node_dict.items():
            with open(name, 'r') as f:
                lines = f.readlines()
            self.lines[name] = lines
            ext = os.path.splitext(name)[1].lstrip('.').lower()
            for line in lines:
                if ext == 'md':
                    raw_links = re.findall(r'\[(?:[^\]]*)\]\(([^)#\s"]+)', line)
                else:
                    raw_links = [m[0] for m in re.findall(r'\[\[([^#|\[\]]+)(#[^|\[\]]*)?(|[^\]]*)?\]\]', line)]
                for link in raw_links:
                    child_node = self.__resolve_link(root, link)
                    self.graph.add_edge(name, child_node)

    def __filter_lines(self, regexes: list, lines: list):
        """
        Returns the number of regexes that matched any of the lines in lines.

        Args:
            regexes (list): List of regular expressions.
            lines (list): List of lines to match.
        """
        matches = 0
        try:
            for regex in regexes:
                for line in lines:
                    if re.search(regex, line.lower()):
                        matches += 1
                        break
        except Exception as e:
            print(e)
        return matches

    def __filter_lines_all(self, regexes: list, lines: list, invert: bool = False):
        if invert:
            return self.__filter_lines(regexes, lines) == 0
        return self.__filter_lines(regexes, lines) == len(regexes)

    def __filter_lines_any(self, regexes: list, lines: list):
        return self.__filter_lines(regexes, lines) > 0


    #----------------------------------------------------------------------------------------------------
    # Graph manipulation methods
    #----------------------------------------------------------------------------------------------------
    def reset_graph(self):
        self.graph = copy.deepcopy(self.original_graph)
        return self

    def reload_graph(self):
        del self.graph, self.original_graph, self.lines
        self.graph = nx.DiGraph()
        self.lines = dict()
        self._canonical = dict()
        node_dict = self.__create_nodes()
        self.__parse_and_add_edges(node_dict)
        self.original_graph = copy.deepcopy(self.graph)

    def add_attribute_by_regex(self, regexes: list, attribute: list, value: list):
        """
        Add attributes with the corresponding values to documents whose contents is matched by a
        conjunction of the specified regexes.

        Args:
            regexes (list)
            attribute (list): list of graphviz attribute names
            value (list): list of corresponding values
        """
        for node in self.graph.nodes:
            lines = self.lines.get(node, list())
            if self.__filter_lines_all(regexes, lines):
                for attr, val in zip(attribute, value):
                    self.graph.nodes[node][attr] = val
        return self

    def filter_filenames(self, regexes: list, invert: bool = False):
        """
        Filter filenames by regexes. All node labels that do not match all of the regular expressions in 'regexes' will be removed. If invert then all
        nodes that match any of the regular expressions will be removed.

        Args:
            regexes (list)
        """
        nodes_to_remove = list()
        for node, data in self.graph.nodes(data=True):
            label = data.get('label')
            debug(f"{node} label: {label} regexes: {regexes}")
            if label and not self.__filter_lines_all(regexes, [data.get('label')], invert=invert):
                nodes_to_remove.append(node)
        self.graph.remove_nodes_from(nodes_to_remove)
        return self

    def filter_nodes(self, regexes: list, invert: bool = False):
        """
        Filters nodes by regexes. All nodes that do not match all of the regular expressions in 'regexes' will be removed. If invert then all nodes that
        match any of the regular expressions will be removed.

        Args:
            regexes (list)
            invert (bool)
        """
        nodes_to_remove = list()
        for node in self.graph.nodes:
            lines = self.lines.get(node, list())
            if not self.__filter_lines_all(regexes, lines, invert=invert):
                nodes_to_remove.append(node)
        self.graph.remove_nodes_from(nodes_to_remove)
        return self

    def collapse_children(self, nodes: list, depth: int = 1):
        """
        All child nodes will be collapsed into this node. Edges from and to children will go to the parent instead.

        Args:
            node (str): Full or relative path of a wiki document.
            depth (int): Number of levels to collapse.
        """
        for node in nodes:
            try:
                node = self.__lookup_node(node)
                self.graph.nodes[node]['is_collapsed'] = True
                children = nx.dfs_successors(self.graph, node, depth)
                for child in np.concatenate(list(children.values())):
                    nx.contracted_nodes(self.graph, node, child, self_loops=False, copy=False)
                del self.graph.nodes[node]['contraction']
            except Exception:
                traceback.print_exc()
        return self

    def add_leaves(self, depth: int = 1):
        """
        Restores leaf nodes from the original graph one layer at a time, repeated `depth` times.
        A node is eligible if it is absent from the current graph but has at least one predecessor
        that is present. Edges between all newly added nodes are also restored.

        Args:
            depth (int): Number of layers of leaves to restore.
        """
        for _ in range(depth):
            current_nodes = set(self.graph.nodes)
            missing = set(self.original_graph.nodes) - current_nodes
            to_add = [n for n in missing if any(p in current_nodes for p in self.original_graph.predecessors(n))]
            if not to_add:
                break
            self.graph.add_nodes_from((n, self.original_graph.nodes[n]) for n in to_add)
            reachable = current_nodes | set(to_add)
            self.graph.add_edges_from(
                (u, v) for u, v in self.original_graph.edges
                if u in reachable and v in reachable and not self.graph.has_edge(u, v)
            )
        return self

    def remove_leaves(self, depth: int = 1):
        """
        Removes leaf nodes (out-degree 0) from the graph, repeated `depth` times.

        Args:
            depth (int): Number of layers of leaves to remove.
        """
        for _ in range(depth):
            leaves = [n for n in self.graph.nodes if self.graph.out_degree(n) == 0]
            self.graph.remove_nodes_from(leaves)
        return self
