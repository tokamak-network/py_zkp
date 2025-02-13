import ast
if 'arg' not in dir(ast):
    ast.arg = type(None)

def parse(code):
    return ast.parse(code).body

# Takes code of the form
# def foo(arg1, arg2 ...):
#     x = arg1 + arg2
#     y = ...
#     return x + y
# And extracts the inputs and the body, where
# it expects the body to be a sequence of
# variable assignments (variables are immutable;
# can only be set once) and a return statement at the end
def extract_inputs_and_body(code):
    o = []
    if len(code) != 1 or not isinstance(code[0], ast.FunctionDef):
        raise Exception("Expecting function declaration")
    # Gather the list of input variables
    inputs = []
    for arg in code[0].args.args:
        if isinstance(arg, ast.arg):
            assert isinstance(arg.arg, str)
            inputs.append(arg.arg)
        elif isinstance(arg, ast.Name):
            inputs.append(arg.id)
        else:
            raise Exception("Invalid arg: %r" % ast.dump(arg))
    # Gather the body
    outputs = []
    body = []
    returned = False
    for c in code[0].body:
        if not isinstance(c, (ast.Assign, ast.Assert, ast.Return)):
            raise Exception("Expected variable assignment or return")
        if returned:
            raise Exception("Cannot do stuff after a return statement")
        if isinstance(c, ast.Return):
            returned = True
            if isinstance(c.value, ast.Tuple):
                outputs = [f'~out_{i}' for i in range(len(c.value.elts))]
            elif c.value is not None:
                outputs = ['~out']
        body.append(c)
    return inputs, body, outputs

# Convert a body with potentially complex expressions into
# simple expressions of the form x = y or x = y * z
def flatten_body(body):
    o = []
    for c in body:
        o.extend(flatten_stmt(c))
    return o

# Generate a dummy variable
next_symbol = [0]
def mksymbol():
    next_symbol[0] += 1
    return 'sym_'+str(next_symbol[0])

def initialize_symbol():
    next_symbol[0] = 0

# Get the value of a node
def get_value(node):
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Constant):
        return node.value
    else:
        raise Exception("Assert comparison must be between variables or constants")

# "Flatten" a single statement into a list of simple statements.
# First extract the target variable, then flatten the expression
def flatten_stmt(stmt):
    # Get target variable
    if isinstance(stmt, ast.Assign):
        assert len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
        target = stmt.targets[0].id

    elif isinstance(stmt, ast.Assert):
        assert isinstance(stmt.test, ast.Compare) and len(stmt.test.ops) == 1 and isinstance(stmt.test.ops[0], ast.Eq), "Only '==' comparison is allowed in assert statements"
        left = get_value(stmt.test.left)
        right = get_value(stmt.test.comparators[0])
        # 두 값이 모두 상수인 경우 미리 검사
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            if left != right:
                raise AssertionError(f"Assert condition not satisfied: {left} != {right}")
        return [['assert', left, right]] #@Todo: assert -> '==' ?

    elif isinstance(stmt, ast.Return):
        if stmt.value is None:
            return []
        elif isinstance(stmt.value, ast.Tuple):
            flattened = []
            for i, elt in enumerate(stmt.value.elts):
                flattened.extend(flatten_expr(f'~out_{i}', elt))
            return flattened
        else:
            return flatten_expr('~out', stmt.value)
    # Get inner content
    return flatten_expr(target, stmt.value)

# Main method for flattening an expression
def flatten_expr(target, expr):
    # x = y
    if isinstance(expr, ast.Name):
        return [['set', target, expr.id]]
    # x = 5
    elif isinstance(expr, ast.Constant):
        return [['set', target, expr.n]]
    # x = y (op) z
    # Or, for that matter, x = y (op) 5
    # 각 연산은 flatcode 1개를 생성
    # 제곱연산은 제곱한 수만큼 flatcode를 생성
    elif isinstance(expr, ast.BinOp):
        if isinstance(expr.op, ast.Add):
            op = '+'
        elif isinstance(expr.op, ast.Mult):
            op = '*'
        elif isinstance(expr.op, ast.Sub):
            op = '-'
        elif isinstance(expr.op, ast.Div):
            op = '/'
        # Exponentiation gets compiled to repeat multiplication,
        # requires constant exponent
        elif isinstance(expr.op, ast.Pow):
            assert isinstance(expr.right, ast.Constant)
            if expr.right.n == 0:
                return [['set', target, 1]]
            elif expr.right.n == 1:
                return flatten_expr(target, expr.left)
            else: # This could be made more efficient via square-and-multiply but oh well
                if isinstance(expr.left, (ast.Name, ast.Constant)):
                    nxt = base = expr.left.id if isinstance(expr.left, ast.Name) else expr.left.n
                    o = []
                else:
                    nxt = base = mksymbol()
                    o = flatten_expr(base, expr.left)
                for i in range(1, expr.right.n):
                    latest = nxt
                    nxt = target if i == expr.right.n - 1 else mksymbol()
                    o.append(['*', nxt, latest, base])
                return o
        elif isinstance(expr, ast.Compare):
            raise Exception("Comparisons are only allowed in assert statements")
        else:
            raise Exception("Bad operation: %r" % ast.dump(expr.op))
        # If the subexpression is a variable or a number, then include it directly
        if isinstance(expr.left, (ast.Name, ast.Constant)):
            var1 = expr.left.id if isinstance(expr.left, ast.Name) else expr.left.n
            sub1 = []

        # If one of the subexpressions is itself a compound expression, recursively
        # apply this method to it using an intermediate variable
        else:
            var1 = mksymbol()
            sub1 = flatten_expr(var1, expr.left)
        # Same for right subexpression as for left subexpression
        if isinstance(expr.right, (ast.Name, ast.Constant)):
            var2 = expr.right.id if isinstance(expr.right, ast.Name) else expr.right.n
            sub2 = []
        else:
            var2 = mksymbol()
            sub2 = flatten_expr(var2, expr.right)
        # Last expression represents the assignment; sub1 and sub2 represent the
        # processing for the subexpression if any
        return sub1 + sub2 + [[op, target, var1, var2]]
    else:
        raise Exception("Unexpected statement value: %r" % ast.dump(expr.value))

# Adds a variable or number into one of the vectors; if it's a variable
# then the slot associated with that variable is set to 1, and if it's
# a number then the slot associated with 1 gets set to that number
def insert_var(arr, varz, var, used, reverse=False):
    if isinstance(var, str):
        if var not in used:
            raise Exception("Using a variable before it is set!")
        arr[varz.index(var)] += (-1 if reverse else 1)
    elif isinstance(var, int):
        arr[0] += var * (-1 if reverse else 1)

# Maps input, output and intermediate variables to indices
def get_var_placement(inputs, flatcode, outputs):
    used_vars = ['~one'] + outputs + inputs
    for c in flatcode:
        if c[0] != 'assert' and c[1] not in used_vars:
            used_vars.append(c[1])
    return used_vars

# Convert the flattened code generated above into a rank-1 constraint system
def flatcode_to_r1cs(inputs, flatcode, outputs):
    varz = get_var_placement(inputs, flatcode, outputs)
    A, B, C = [], [], []
    used = {i: True for i in inputs}
    # 일단 파라미터는 무조건 used로 바꾸고.
    for x in flatcode:
    # 모든 flatcode를 대상으로 수행. flatcode의 갯수만큼 행의 row갯수(컬럼 높이)가 결정된다.
        a, b, c = [0] * len(varz), [0] * len(varz), [0] * len(varz)
        # assert는 이미 used된 변수를 비교하기위해 사용
        if x[0] != 'assert':
            if x[1] in used:
                raise Exception("Variable already used: %r" % x[1])
            used[x[1]] = True
        # 타겟 자리도 used로 바꾸고 시작
        if x[0] == 'set':
            # a의 타겟 자리에 1을 더하고, 우항(value) 자리를 -1로 바꾼다
            a[varz.index(x[1])] += 1
            insert_var(a, varz, x[2], used, reverse=True)
            b[0] = 1
        elif x[0] == '+' or x[0] == '-':
            # c행렬의 타겟 자리를 1로 바꾸고, left는 a자리에서 1, right는 a자리에서 1(-1 if "-"라면)로 만듬
            # 더하기이기 때문에 이렇게 하면 항상 양쪽 식이 만족함
            c[varz.index(x[1])] = 1
            insert_var(a, varz, x[2], used)
            insert_var(a, varz, x[3], used, reverse=(x[0] == '-'))
            b[0] = 1
        elif x[0] == '*':
            # c행렬의 타겟 자리는 1로 만들고
            # a행렬의 left 자리를 1로, b행렬의 right 자리를 1로 만든다
            c[varz.index(x[1])] = 1
            insert_var(a, varz, x[2], used)
            insert_var(b, varz, x[3], used)
        elif x[0] == '/':
            # a행렬의 타겟 자리를 1로 만들고
            # c행렬의 left자리를 1로, b행렬의 right자리를 1로 바꾼
            # why? a = b / c --> b = a * c가 되기 때문
            insert_var(c, varz, x[2], used)
            a[varz.index(x[1])] = 1
            insert_var(b, varz, x[3], used)
        elif x[0] == 'assert':
            # assert 문 처리
            insert_var(a, varz, x[1], used)
            b[0] = 1
            insert_var(c, varz, x[2], used)
        A.append(a)
        B.append(b)
        C.append(c)
    return A, B, C

# Get a variable or number given an existing input vector
def grab_var(varz, assignment, var):
    if isinstance(var, str):
        return assignment[varz.index(var)]
    elif isinstance(var, int):
        return var
    else:
        raise Exception("What kind of expression is this? %r" % var)

# Goes through flattened code and completes the input vector
# 특정한 인풋값에 의해서 인풋백터를 완성함
def assign_variables(inputs, input_vars, flatcode, outputs):
    varz = get_var_placement(inputs, flatcode, outputs)
    assignment = [0] * len(varz)
    assignment[0] = 1
    for i, inp in enumerate(input_vars):
        assignment[len(outputs) + i + 1] = inp
    for x in flatcode:
        if x[0] == 'set':
            assignment[varz.index(x[1])] = grab_var(varz, assignment, x[2])
        elif x[0] == '+':
            assignment[varz.index(x[1])] = grab_var(varz, assignment, x[2]) + grab_var(varz, assignment, x[3])
        elif x[0] == '-':
            assignment[varz.index(x[1])] = grab_var(varz, assignment, x[2]) - grab_var(varz, assignment, x[3])
        elif x[0] == '*':
            assignment[varz.index(x[1])] = grab_var(varz, assignment, x[2]) * grab_var(varz, assignment, x[3])
        elif x[0] == '/':
            assignment[varz.index(x[1])] = grab_var(varz, assignment, x[2]) / grab_var(varz, assignment, x[3])
    return assignment


def code_to_r1cs_with_inputs(code, input_vars):
    inputs, body, outputs = extract_inputs_and_body(parse(code))
    print('Inputs')
    print(inputs)
    print('Body')
    print(body)
    flatcode = flatten_body(body)
    print('Flatcode')
    print(flatcode)
    print('Input var assignment')
    print(get_var_placement(inputs, flatcode, outputs))
    A, B, C = flatcode_to_r1cs(inputs, flatcode, outputs)
    r = assign_variables(inputs, input_vars, flatcode, outputs)
    return r, A, B, C



r, A, B, C = code_to_r1cs_with_inputs("""
def qeval(x):
    y = x**3
    return y+x+5, x, x+x+x
""", [3])
# print('r')
# print(r)
# print('A')
# for x in A: print(x)
# print('B')
# for x in B: print(x)
# print('C')
# for x in C: print(x)

# Inputs
# ['x']
# Body
# [<_ast.Assign object at 0x7fb0fa506b70>, <_ast.Return object at 0x7fb0fa506cc0>]
# Flatcode
# [['*', 'sym_1', 'x', 'x'], ['*', 'y', 'sym_1', 'x'], ['+', 'sym_2', 'y', 'x'], ['+', '~out', 'sym_2', 5]]
# Input var assignment
# ['~one', 'x', '~out', 'sym_1', 'y', 'sym_2']
# r
# [1, 3, 35, 9, 27, 30]
# A
# [0, 1, 0, 0, 0, 0]
# [0, 0, 0, 1, 0, 0]
# [0, 1, 0, 0, 1, 0]
# [5, 0, 0, 0, 0, 1]
# B
# [0, 1, 0, 0, 0, 0]
# [0, 1, 0, 0, 0, 0]
# [1, 0, 0, 0, 0, 0]
# [1, 0, 0, 0, 0, 0]
# C
# [0, 0, 0, 1, 0, 0]
# [0, 0, 0, 0, 1, 0]
# [0, 0, 0, 0, 0, 1]
# [0, 0, 1, 0, 0, 0]