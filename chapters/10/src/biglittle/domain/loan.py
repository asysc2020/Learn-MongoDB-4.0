"""
biglittle.domain.loan
Description: module which interacts with the "loans" collection
"""

import pymongo
import decimal
from config.config import Config
from pymongo.cursor import CursorType
from decimal import Decimal
from bson.decimal128 import Decimal128
from utils.utils import Utils
from biglittle.domain.base import Base
from biglittle.entity.loan import Payment, LoanInfo, Loan

class LoanService(Base) :

    # defaults
    collectName = 'biglittle.loans'

    """
    Generates a proposal
    Formula:
        M = P * ( J / (1 - (1 + J)**-N))
        M == monthly payment
        P == principal
        J == effective rate
        N == total number of payments
    @param float principal
    @param int numPayments
    @param float annualRate
    @param string currency
    @param string borrowerKey
    @param string lenderKey
    @param string lenderName
    @param string lenderBusiness
    @return dict
    """
    def generateProposal(self, principal, numPayments, annualRate, currency, borrowerKey, lenderKey, lenderName, lenderBusiness) :
        # calc effective rate and monthly payment
        effective_rate  = annualRate / 100 / 12;
        monthly_payment = principal * ( effective_rate / (1 - (1 + effective_rate) ** -numPayments ))
        # create LoanInfo and Loan documents
        loanInfo = {
            'principal'     : principal,
            'numPayments'   : numPayments,
            'annualRate'    : annualRate,
            'effectiveRate' : effective_rate * 1000,
            'currency'      : currency,
            'monthlyPymt'   : monthly_payment
        }
        loan = {
            'borrowerKey'    : borrowerKey,
            'lenderKey'      : lenderKey,
            'lenderName'     : lenderName,
            'lenderBusiness' : lenderBusiness,
            'overpayment'    : 0.00,
            'loanInfo'       : loanInfo,
            'payments'       : []
        }
        return loan

    """
    Generates a series of simulated proposals
    @param float principal
    @param int numPayments
    @param list lenders : list of lender keys
    @return dict
    """
    def generateMany(self, principal, numPayments, currency, borrowerKey, lenders) :
        proposals = {}
        for item in lenders :
            # pick an annual rate at random
            import random
            annualRate = random.randint(1000,20000) / 1000
            # add a new proposal
            doc = self.generateProposal(principal, numPayments, annualRate, currency, borrowerKey, item['key'], item['name'], item['business'])
            proposals.update({ item['key'] : doc })
        return proposals

    """
    Generates loan key
    @param biglittle.entity.loan.Loan loan
    @return string loanKey
    """
    def generateLoanKey(self, loan) :
        from time import gmtime, strftime
        date = strftime('%Y%m%d', gmtime())
        loan['loanKey'] = loan['borrowerKey'] + '_' + loan['lenderKey'] + '_' + date
        return loan

    """
    Saves loan document
    @param biglittle.entity.loan.Loan loan
    @return bool True if success else False
    """
    def save(self, loan) :
        # generate loanKey
        loan = self.generateLoanKey(loan)
        # convert values to NumberDecimal
        loan.convertDecimalToBson()
        # save
        return self.collection.insert(loan)

    """
    Retrieves amount due for given borrower
    @param string borrowerKey
    @return Decimal amtDue
    """
    def fetchAmtDueForBorrower(self,  borrowerKey) :
        amtDue = Decimal(0.00)
        loan = self.collection.find_one({"borrowerKey":borrowerKey})
        if loan :
            loanInfo = loan.getLoanInfo()
            amtDue = loanInfo.get('monthlyPymt').to_decimal()
        return amtDue

    """
    Retrieves loan for given borrower
    Converts all BSON Decimal128 financial data fields to Decimal
    @param string borrowerKey
    @return biglittle.entity.users.User instance
    """
    def fetchLoanByBorrowerKey(self,  borrowerKey) :
        loan = self.collection.find_one({"borrowerKey":borrowerKey})
        loan.convertBsonToDecimal()
        return loan

    """
    Looks for next scheduled payment for this borrower
    @param string borrowerKey
    @param float amtPaid
    @param biglittle.entity.loan.Loan loan
    @return bool True if payment processed OK | False otherwise
    """
    def processPayment(self, borrowerKey, amtPaid, loan) :

        # init vars
        config = Config()
        utils  = Utils()
        result = False
        loanInfo = loan.getLoanInfo()
        amtDue   = loanInfo.getMonthlyPayment()
        overpayment = 0.00

        # convert amount paid to decimal.Decimal for processing purposes
        if not isinstance(amtPaid, Decimal) :
            amtPaid = Decimal(amtPaid)

        # find first payment where nothing has been paid
        for doc in loan['payments'] :
            if doc['amountPaid'] == 0 :
                # if underpayment, add to "overpayment" but do no further processing
                if amtPaid < amtDue :
                    overpayment = amtPaid
                else :
                    overpayment = amtPaid - amtDue
                    # apply payment
                    doc['amountPaid'] = doc['amountDue']
                    doc['status'] = 'received'
                    # save var today
                    from time import gmtime, strftime
                    now = strftime('%Y-%m-%d', gmtime())
                    doc['recvdate'] = now
                break

        # update overpayment field
        currentOver = loan.get('overpayment')
        loan.set('overpayment', currentOver + overpayment)

        # convert values to NumberDecimal
        loan.convertDecimalToBson()

        # update by replacement
        filt = { 'borrowerKey' : borrowerKey }
        result = self.collection.replace_one(filt,loan)

        # send out pub/sub notifications
        if result :
            # convert values to Decimal
            loan.convertBsonToDecimal()
            # have publisher notify subscribers
            self.publisher.trigger(self.publisher.EVENT_LOAN_UPDATE_BORROWER, {'loan':loan, 'amtPaid':amtPaid})

        # done
        return result
