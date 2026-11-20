import ast

def find_return_in_loops_and_funcs(filepath):
    with open(filepath, 'r', encoding='utf-8') as file:
        source = file.read()
    tree = ast.parse(source)
    results = []

    class ReturnVisitor(ast.NodeVisitor):
        def __init__(self, parent_type=None):
            self.parent_type = parent_type

        def visit_FunctionDef(self, node):
            rv = ReturnVisitor('function')
            rv.generic_visit(node)

        def visit_While(self, node):
            rv = ReturnVisitor('while')
            rv.generic_visit(node)

        def visit_For(self, node):
            rv = ReturnVisitor('for')
            rv.generic_visit(node)

        def visit_Return(self, node):
            # pega a linha do return e tipo de bloco pai
            if self.parent_type in ('while', 'for'):
                results.append((node.lineno, self.parent_type))

        def generic_visit(self, node):
            previous_type = self.parent_type
            if isinstance(node, (ast.FunctionDef, ast.While, ast.For)):
                if isinstance(node, ast.FunctionDef):
                    self.parent_type = 'function'
                elif isinstance(node, ast.While):
                    self.parent_type = 'while'
                elif isinstance(node, ast.For):
                    self.parent_type = 'for'
            super().generic_visit(node)
            self.parent_type = previous_type

    ReturnVisitor().visit(tree)
    return results

if __name__ == "__main__":
    caminho = "trading_bot_geneses.py"  # Troque para o arquivo que quer checar
    retorno = find_return_in_loops_and_funcs(caminho)
    if not retorno:
        print("Nenhum return suspeito encontrado em loops.")
    else:
        for linha, tipo in retorno:
            print(f"Return encontrado dentro de um {tipo.upper()} na linha {linha}")
