import os

import json

from eth_utils import (
    int_to_big_endian,
    decode_hex,
)

from evm.exceptions import (
    IncorrectContractCreationAddress,
    ContractCreationCollision,
)
from evm.utils.address import generate_CREATE2_contract_address
from evm.utils.padding import pad32

from tests.core.helpers import (
    new_sharding_transaction,
)


DIR = os.path.dirname(__file__)


def test_sharding_apply_transaction(unvalidated_shard_chain):  # noqa: F811
    chain = unvalidated_shard_chain

    CREATE2_contracts = json.load(
        open(os.path.join(DIR, '../contract_fixtures/CREATE2_contracts.json'))
    )
    simple_transfer_contract = CREATE2_contracts["simple_transfer_contract"]
    CREATE2_contract = CREATE2_contracts["CREATE2_contract"]
    simple_factory_contract_bytecode = CREATE2_contracts["simple_factory_contract"]["bytecode"]

    # First test: simple ether transfer contract
    first_deploy_tx = new_sharding_transaction(
        tx_initiator=decode_hex(simple_transfer_contract['address']),
        data_destination=b'',
        data_value=0,
        data_msgdata=b'',
        data_vrs=b'',
        code=simple_transfer_contract['bytecode'],
    )

    vm = chain.get_vm()
    computation, _ = vm.apply_transaction(first_deploy_tx)
    assert not computation.is_error
    gas_used = vm.block.header.gas_used
    assert gas_used > first_deploy_tx.intrinsic_gas
    last_gas_used = gas_used

    # Transfer ether to recipient
    recipient = decode_hex('0xa94f5374fce5edbc8e2a8697c15331677e6ebf0c')
    amount = 100
    tx_initiator = decode_hex(simple_transfer_contract['address'])
    transfer_tx = new_sharding_transaction(tx_initiator, recipient, amount, b'', b'')

    computation, _ = vm.apply_transaction(transfer_tx)
    assert not computation.is_error
    gas_used = vm.block.header.gas_used - last_gas_used
    assert gas_used > transfer_tx.intrinsic_gas
    last_gas_used = vm.block.header.gas_used
    with vm.state.state_db(read_only=True) as state_db:
        assert state_db.get_balance(recipient) == amount

    # Second test: contract that deploy new contract with CREATE2
    second_deploy_tx = new_sharding_transaction(
        tx_initiator=decode_hex(CREATE2_contract['address']),
        data_destination=b'',
        data_value=0,
        data_msgdata=b'',
        data_vrs=b'',
        code=CREATE2_contract['bytecode'],
    )

    computation, _ = vm.apply_transaction(second_deploy_tx)
    assert not computation.is_error
    gas_used = vm.block.header.gas_used - last_gas_used
    assert gas_used > second_deploy_tx.intrinsic_gas
    last_gas_used = vm.block.header.gas_used

    # Invoke the contract to deploy new contract
    tx_initiator = decode_hex(CREATE2_contract['address'])
    newly_deployed_contract_address = generate_CREATE2_contract_address(
        int_to_big_endian(0),
        decode_hex(simple_factory_contract_bytecode)
    )
    invoke_tx = new_sharding_transaction(
        tx_initiator,
        b'',
        0,
        b'',
        b'',
        access_list=[[tx_initiator, pad32(b'')], [newly_deployed_contract_address]]
    )

    computation, _ = vm.apply_transaction(invoke_tx)
    assert not computation.is_error
    gas_used = vm.block.header.gas_used - last_gas_used
    assert gas_used > invoke_tx.intrinsic_gas
    with vm.state.state_db(read_only=True) as state_db:
        newly_deployed_contract_address = generate_CREATE2_contract_address(
            int_to_big_endian(0),
            decode_hex(simple_factory_contract_bytecode)
        )
        assert state_db.get_code(newly_deployed_contract_address) == b'\xbe\xef'
        assert state_db.get_storage(decode_hex(CREATE2_contract['address']), 0) == 1


def test_CREATE2_deploy_contract_edge_cases(unvalidated_shard_chain):  # noqa: F811
    CREATE2_contracts = json.load(
        open(os.path.join(DIR, '../contract_fixtures/CREATE2_contracts.json'))
    )
    simple_transfer_contract = CREATE2_contracts["simple_transfer_contract"]

    # First case: computed contract address not the same as provided in `transaction.to`
    chain = unvalidated_shard_chain
    code = "0xf3"
    computed_address = generate_CREATE2_contract_address(b"", decode_hex(code))
    first_failed_deploy_tx = new_sharding_transaction(
        tx_initiator=decode_hex(simple_transfer_contract['address']),
        data_destination=b'',
        data_value=0,
        data_msgdata=b'',
        data_vrs=b'',
        code=code,
        access_list=[[decode_hex(simple_transfer_contract['address'])], [computed_address]]
    )

    vm = chain.get_vm()
    computation, _ = vm.apply_transaction(first_failed_deploy_tx)
    assert isinstance(computation._error, IncorrectContractCreationAddress)
    gas_used = vm.block.header.gas_used
    assert gas_used > first_failed_deploy_tx.intrinsic_gas
    last_gas_used = gas_used

    # Next, complete deploying the contract
    successful_deploy_tx = new_sharding_transaction(
        tx_initiator=decode_hex(simple_transfer_contract['address']),
        data_destination=b'',
        data_value=0,
        data_msgdata=b'',
        data_vrs=b'',
        code=simple_transfer_contract['bytecode'],
    )
    computation, _ = vm.apply_transaction(successful_deploy_tx)
    assert not computation.is_error
    gas_used = vm.block.header.gas_used - last_gas_used
    assert gas_used > successful_deploy_tx.intrinsic_gas
    last_gas_used = gas_used

    # Second case: deploy to existing account
    second_failed_deploy_tx = successful_deploy_tx
    computation, _ = vm.apply_transaction(second_failed_deploy_tx)
    assert isinstance(computation._error, ContractCreationCollision)
    gas_used = vm.block.header.gas_used - last_gas_used
    assert gas_used > second_failed_deploy_tx.intrinsic_gas
